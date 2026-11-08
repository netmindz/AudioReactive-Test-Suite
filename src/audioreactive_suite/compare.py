"""Time-based capture comparison and verdict generation."""

from __future__ import annotations

import json
import math
import statistics
import wave
from bisect import bisect_left
from pathlib import Path
from typing import Any, Callable, Iterable

from .capture import load_capture
from .bands import geq_bands
from .model import compare_to_models
from .packet import DecodedPacket, decode_packet


EXIT_PASS = 0
EXIT_THRESHOLD_FAILURE = 2
EXIT_INCONCLUSIVE = 3
REPORT_SCHEMA_VERSION = 1


def load_case(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as file:
        case = json.load(file)
    if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
        raise ValueError("case requires a string id")
    if case.get("schema_version", 1) != 1:
        raise ValueError("unsupported case schema")
    if not isinstance(case.get("thresholds"), dict):
        raise ValueError("case thresholds must be an object")
    if not case["thresholds"] and not case.get("shape_thresholds") and not case.get("level_thresholds"):
        raise ValueError("case requires at least one raw, shape, or level threshold")
    if not isinstance(case.get("required_settings", []), list) or not all(
        isinstance(setting, str) and setting for setting in case.get("required_settings", [])
    ):
        raise ValueError("required_settings must be a list of non-empty strings")
    if not isinstance(case.get("stimulus", {}), dict):
        raise ValueError("stimulus must be an object")
    return case


def _decode(artifact: dict[str, Any]) -> tuple[list[DecodedPacket], list[str]]:
    packets: list[DecodedPacket] = []
    errors: list[str] = []
    for index, record in enumerate(artifact["packets"]):
        try:
            packets.append(decode_packet(record))
        except ValueError as error:
            errors.append(f"packet {index}: {error}")
    return sorted(packets, key=lambda packet: packet.timestamp_ns), errors


def _identity_errors(label: str, artifact: dict[str, Any], packets: list[DecodedPacket]) -> list[str]:
    metadata = artifact["metadata"]
    errors: list[str] = []
    if not isinstance(metadata.get("identity"), str) or not metadata["identity"]:
        errors.append(f"{label} capture has no explicit identity")
    sources = {packet.source for packet in packets}
    if len(sources) != 1:
        errors.append(f"{label} capture has {len(sources)} sources; exactly one is required")
    return errors


def _settings_errors(reference: dict[str, Any], candidate: dict[str, Any], case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    reference_settings = reference["metadata"].get("settings", {})
    candidate_settings = candidate["metadata"].get("settings", {})
    if not isinstance(reference_settings, dict) or not isinstance(candidate_settings, dict):
        return ["capture settings must be objects"]
    for key in case.get("required_settings", []):
        if key not in reference_settings or key not in candidate_settings:
            errors.append(f"required setting {key!r} is unknown")
        elif reference_settings[key] != candidate_settings[key]:
            errors.append(f"required setting {key!r} differs")
    return errors


def _window_packets(
    reference: list[DecodedPacket],
    candidate: list[DecodedPacket],
    offset_ns: int,
    case: dict[str, Any],
    origin_ns: int | None = None,
) -> tuple[list[DecodedPacket], list[DecodedPacket], dict[str, Any]]:
    if not reference or not candidate:
        return [], [], {"reason": "one stream has no valid packets"}

    candidate_times = [packet.timestamp_ns + offset_ns for packet in candidate]
    overlap_start = max(reference[0].timestamp_ns, candidate_times[0])
    overlap_end = min(reference[-1].timestamp_ns, candidate_times[-1])
    window = case.get("measurement_window_ms", {})
    if not isinstance(window, dict):
        raise ValueError("measurement_window_ms must be an object")
    stimulus = case.get("stimulus", {})
    if not isinstance(stimulus, dict):
        raise ValueError("stimulus must be an object")
    origin = origin_ns if origin_ns is not None else reference[0].timestamp_ns
    warmup_ms = float(stimulus.get("warmup_seconds", 0)) * 1000
    start_ms = float(window.get("start", warmup_ms))
    default_end_ms = (
        start_ms + float(stimulus["measurement_seconds"]) * 1000
        if isinstance(stimulus.get("measurement_seconds"), (int, float))
        else (reference[-1].timestamp_ns - origin) / 1_000_000
    )
    end_ms = float(window.get("end", default_end_ms))
    start = max(overlap_start, origin + int(start_ms * 1_000_000))
    end = min(overlap_end, origin + int(end_ms * 1_000_000))
    selected_reference = [packet for packet in reference if start <= packet.timestamp_ns <= end]
    selected_candidate = [
        packet for packet, shifted_time in zip(candidate, candidate_times) if start <= shifted_time <= end
    ]
    return selected_reference, selected_candidate, {
        "start_ns": start,
        "end_ns": end,
        "origin_ns": origin,
        "origin_source": "playback_started_monotonic_ns" if origin_ns is not None else "first_reference_packet",
        "requested_start_ms": start_ms,
        "requested_end_ms": end_ms,
        "overlap_duration_ms": max(0, end - start) / 1_000_000,
    }


def _interpolate_at(
    packets: list[DecodedPacket], times: list[int], timestamp_ns: int, maximum_gap_ns: int | None = None
) -> DecodedPacket | None:
    index = bisect_left(times, timestamp_ns)
    if index == 0:
        if timestamp_ns == times[0] or (
            maximum_gap_ns is not None and times[0] - timestamp_ns <= maximum_gap_ns / 2
        ):
            return packets[0]
        return None
    if index == len(packets):
        if timestamp_ns == times[-1] or (
            maximum_gap_ns is not None and timestamp_ns - times[-1] <= maximum_gap_ns / 2
        ):
            return packets[-1]
        return None
    before, after = packets[index - 1], packets[index]
    if after.timestamp_ns == before.timestamp_ns:
        return before
    if maximum_gap_ns is not None and after.timestamp_ns - before.timestamp_ns > maximum_gap_ns:
        return None
    fraction = (timestamp_ns - before.timestamp_ns) / (after.timestamp_ns - before.timestamp_ns)

    def blend(left: float, right: float) -> float:
        return left + fraction * (right - left)

    nearest = before if fraction < 0.5 else after
    return DecodedPacket(
        timestamp_ns=timestamp_ns,
        source=nearest.source,
        volume_raw=blend(before.volume_raw, after.volume_raw),
        volume_smoothed=blend(before.volume_smoothed, after.volume_smoothed),
        peak=nearest.peak,
        fft=tuple(blend(left, right) for left, right in zip(before.fft, after.fft)),
        magnitude=blend(before.magnitude, after.magnitude),
        dominant_frequency=blend(before.dominant_frequency, after.dominant_frequency),
        frame_counter=nearest.frame_counter,
        extension=nearest.extension,
    )


def _time_pairs(
    reference: list[DecodedPacket],
    candidate: list[DecodedPacket],
    offset_ns: int,
    maximum_distance_ns: int,
) -> list[tuple[DecodedPacket, DecodedPacket]]:
    if len(candidate) < 2:
        return []
    candidate_times = [packet.timestamp_ns for packet in candidate]
    median_interval = statistics.median(
        right.timestamp_ns - left.timestamp_ns for left, right in zip(candidate, candidate[1:])
    )
    maximum_gap_ns = min(int(median_interval * 3), max(1, maximum_distance_ns * 2))
    pairs = []
    for ref_packet in reference:
        candidate_packet = _interpolate_at(
            candidate, candidate_times, ref_packet.timestamp_ns - offset_ns, maximum_gap_ns
        )
        if candidate_packet is not None:
            pairs.append((ref_packet, candidate_packet))
    return pairs


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    left_norm = math.sqrt(sum(value * value for value in left_centered))
    right_norm = math.sqrt(sum(value * value for value in right_centered))
    if left_norm <= 1e-9 or right_norm <= 1e-9:
        return None
    return sum(a * b for a, b in zip(left_centered, right_centered)) / (left_norm * right_norm)


def _estimate_offset(
    reference: list[DecodedPacket],
    candidate: list[DecodedPacket],
    search_range_ms: float,
    step_ms: float,
    window_start_ns: int,
    window_end_ns: int,
) -> tuple[int, dict[str, Any]]:
    if len(reference) < 3 or len(candidate) < 3:
        return 0, {"method": "envelope_cross_correlation", "confidence": 0.0, "ambiguous": True}
    ref_interval = statistics.median(
        right.timestamp_ns - left.timestamp_ns for left, right in zip(reference, reference[1:])
    )
    grid_step = max(1_000_000, int(ref_interval))
    first = max(reference[0].timestamp_ns, window_start_ns)
    last = min(reference[-1].timestamp_ns, window_end_ns)
    grid = list(range(first, last + 1, grid_step))
    reference_times = [packet.timestamp_ns for packet in reference]
    candidate_times = [packet.timestamp_ns for packet in candidate]
    range_ns = int(search_range_ms * 1_000_000)
    offset_step_ns = max(1, int(step_ms * 1_000_000))
    offsets = list(range(-range_ns, range_ns + 1, offset_step_ns))
    scores: list[tuple[float, int, int]] = []
    feature_fields = [("volume_smoothed", lambda packet: packet.volume_smoothed)] + [
        (f"fft.{band}", lambda packet, band=band: packet.fft[band]) for band in range(16)
    ]
    for offset in offsets:
        usable: list[float] = []
        correlations: list[float] = []
        for _, extractor in feature_fields:
            ref_values = []
            candidate_values = []
            for timestamp in grid:
                ref_packet = _interpolate_at(reference, reference_times, timestamp)
                candidate_packet = _interpolate_at(candidate, candidate_times, timestamp - offset)
                if ref_packet is not None and candidate_packet is not None:
                    ref_values.append(float(extractor(ref_packet)))
                    candidate_values.append(float(extractor(candidate_packet)))
            if len(ref_values) < max(5, len(grid) // 4):
                continue
            correlation = _correlation(ref_values, candidate_values)
            if correlation is not None:
                correlations.append(correlation)
                usable.append(1.0)
        scores.append((sum(correlations) / len(correlations) if correlations else -1.0, offset, len(usable)))

    best_score, best_offset, dimensions = max(scores, key=lambda score: score[0])
    exclusion_ns = max(offset_step_ns * 2, 30_000_000)
    alternatives = [
        score for score, offset, _ in scores if abs(offset - best_offset) > exclusion_ns and score >= 0
    ]
    runner_up = max(alternatives, default=0.0)
    margin = best_score - runner_up
    ambiguous = dimensions == 0 or best_score < 0.35 or margin < 0.015
    return best_offset, {
        "method": "envelope_cross_correlation",
        "confidence": max(0.0, best_score),
        "margin": max(0.0, margin),
        "usable_features": dimensions,
        "search_range_ms": search_range_ms,
        "step_ms": step_ms,
        "ambiguous": ambiguous,
    }


def _nearest_pairs(
    reference: list[DecodedPacket],
    candidate: list[DecodedPacket],
    offset_ns: int,
    maximum_distance_ns: int,
) -> list[tuple[DecodedPacket, DecodedPacket]]:
    pairs: list[tuple[DecodedPacket, DecodedPacket]] = []
    index = 0
    for ref_packet in reference:
        while index + 1 < len(candidate) and abs(
            candidate[index + 1].timestamp_ns + offset_ns - ref_packet.timestamp_ns
        ) <= abs(candidate[index].timestamp_ns + offset_ns - ref_packet.timestamp_ns):
            index += 1
        candidate_packet = candidate[index]
        if abs(candidate_packet.timestamp_ns + offset_ns - ref_packet.timestamp_ns) <= maximum_distance_ns:
            pairs.append((ref_packet, candidate_packet))
    return pairs


def _field_metrics(
    pairs: Iterable[tuple[DecodedPacket, DecodedPacket]], field: str, extractor: Callable[[DecodedPacket], float]
) -> dict[str, float]:
    differences = [extractor(candidate) - extractor(reference) for reference, candidate in pairs]
    if not differences:
        return {}
    return {
        "mean_signed_error": sum(differences) / len(differences),
        "mae": sum(abs(value) for value in differences) / len(differences),
        "rmse": math.sqrt(sum(value * value for value in differences) / len(differences)),
        "max_abs_error": max(abs(value) for value in differences),
        "samples": len(differences),
    }


def _band_shape_level(
    pairs: list[tuple[DecodedPacket, DecodedPacket]], stimulus_type: str | None
) -> dict[str, Any]:
    if not pairs:
        return {"bands": {}, "summary": {}}
    reference_profile = [
        sum(reference.fft[band] for reference, _ in pairs) / len(pairs) for band in range(16)
    ]
    candidate_profile = [
        sum(candidate.fft[band] for _, candidate in pairs) / len(pairs) for band in range(16)
    ]
    reference_total = sum(reference_profile)
    candidate_total = sum(candidate_profile)
    band_metrics: dict[str, Any] = {}
    correlations: list[float] = []
    ratio_errors: list[float] = []
    profile_errors: list[float] = []
    dynamic_stimulus = stimulus_type in {
        "amplitude_modulation",
        "level_steps",
        "logarithmic_sweep",
        "click_train",
    }
    for band in range(16):
        reference_values = [reference.fft[band] for reference, _ in pairs]
        candidate_values = [candidate.fft[band] for _, candidate in pairs]
        correlation = _correlation(reference_values, candidate_values) if dynamic_stimulus else None
        if correlation is not None:
            correlations.append(correlation)
        reference_level = reference_profile[band]
        candidate_level = candidate_profile[band]
        output_value_ratio = candidate_level / reference_level if reference_level > 0 else None
        reference_share = reference_level / reference_total if reference_total > 0 else None
        candidate_share = candidate_level / candidate_total if candidate_total > 0 else None
        profile_error = (
            abs(candidate_share - reference_share)
            if candidate_share is not None and reference_share is not None
            else None
        )
        if output_value_ratio is not None:
            ratio_errors.append(abs(output_value_ratio - 1))
        if profile_error is not None:
            profile_errors.append(profile_error)
        band_metrics[str(band)] = {
            "shape_correlation": correlation,
            "output_value_ratio": output_value_ratio,
            "reference_profile_share": reference_share,
            "candidate_profile_share": candidate_share,
            "normalized_profile_error": profile_error,
        }
    return {
        "bands": band_metrics,
        "summary": {
            "mean_shape_correlation": sum(correlations) / len(correlations) if correlations else None,
            "mean_absolute_output_value_ratio_error": (
                sum(ratio_errors) / len(ratio_errors) if ratio_errors else None
            ),
            "normalized_profile_mae": sum(profile_errors) / len(profile_errors) if profile_errors else None,
            "total_variation": sum(profile_errors) / 2 if profile_errors else None,
            "temporal_correlation_applicable": dynamic_stimulus,
            "level_check": "not_calibrated",
            "interpretation": "shape separates temporal/profile agreement from overall gain or input-level differences",
        },
    }


def _peak_events(packets: list[DecodedPacket]) -> list[int]:
    events: list[int] = []
    previously_high = False
    for packet in packets:
        if packet.peak and not previously_high:
            events.append(packet.timestamp_ns)
        previously_high = packet.peak
    return events


def _peak_metrics(
    reference: list[DecodedPacket], candidate: list[DecodedPacket], offset_ns: int, tolerance_ns: int
) -> dict[str, int]:
    reference_events = _peak_events(reference)
    candidate_events = [timestamp + offset_ns for timestamp in _peak_events(candidate)]
    candidate_index = 0
    matched = 0
    for reference_event in reference_events:
        while candidate_index < len(candidate_events) and candidate_events[candidate_index] < reference_event - tolerance_ns:
            candidate_index += 1
        if candidate_index < len(candidate_events) and abs(candidate_events[candidate_index] - reference_event) <= tolerance_ns:
            matched += 1
            candidate_index += 1
    return {
        "reference_events": len(reference_events),
        "candidate_events": len(candidate_events),
        "matched": matched,
        "missed": len(reference_events) - matched,
        "extra": len(candidate_events) - matched,
    }


def _transport_quality(packets: list[DecodedPacket], decode_errors: list[str]) -> dict[str, float | int]:
    timestamps = [packet.timestamp_ns for packet in packets]
    intervals_ms = [(later - earlier) / 1_000_000 for earlier, later in zip(timestamps, timestamps[1:])]
    duration_seconds = (timestamps[-1] - timestamps[0]) / 1_000_000_000 if len(timestamps) > 1 else 0
    mean_interval = sum(intervals_ms) / len(intervals_ms) if intervals_ms else 0
    frame_values = [packet.frame_counter for packet in packets]
    frame_changes = [((later - earlier) % 256) for earlier, later in zip(frame_values, frame_values[1:])]
    advancing_counter = any(change != 0 for change in frame_changes)
    estimated_loss = sum(change - 1 for change in frame_changes if 1 < change <= 127) if advancing_counter else None
    reordered_counter_steps = sum(change > 127 for change in frame_changes) if advancing_counter else None
    return {
        "valid_packets": len(packets),
        "decode_errors": len(decode_errors),
        "duration_seconds": duration_seconds,
        "packet_rate_hz": (len(packets) - 1) / duration_seconds if duration_seconds > 0 else 0,
        "mean_interval_ms": mean_interval,
        "arrival_jitter_ms": (
            math.sqrt(sum((interval - mean_interval) ** 2 for interval in intervals_ms) / len(intervals_ms))
            if intervals_ms
            else 0
        ),
        "duplicate_timestamps": len(timestamps) - len(set(timestamps)),
        "frame_counter_observed": advancing_counter,
        "estimated_sequence_loss": estimated_loss,
        "frame_counter_reordered_steps": reordered_counter_steps,
    }


def _diagnostics(
    metrics: dict[str, dict[str, float | int]],
    failures: list[dict[str, Any]],
    case: dict[str, Any],
    pairs: list[tuple[DecodedPacket, DecodedPacket]],
    reference_settings: dict[str, Any],
) -> list[dict[str, str]]:
    failed_fields = {failure["field"] for failure in failures if "field" in failure}
    hints: list[dict[str, str]] = []
    failed_bands = [field for field in failed_fields if field.startswith("fft.")]
    stimulus = case.get("stimulus", {})
    stimulus_type = stimulus.get("type") if isinstance(stimulus, dict) else None
    shape_failed = any(failure.get("field") == "shape" for failure in failures)
    if failed_bands or shape_failed or stimulus_type in {
        "tone",
        "white_noise",
        "pink_noise",
        "logarithmic_sweep",
    }:
        if stimulus_type == "tone" and isinstance(stimulus.get("frequency_hz"), (int, float)):
            sample_rate = reference_settings.get("sample_rate_hz", 22_050)
            fft_size = reference_settings.get("fft_size", 512)
            frequency_bin = round(stimulus["frequency_hz"] * fft_size / sample_rate)
            ranges = geq_bands(
                reference_settings.get("frequency_distribution", 0),
                bool(reference_settings.get("use_input_filter", False)),
            )
            expected_band = next(
                (band for band, (low, high, _) in enumerate(ranges) if low <= frequency_bin <= high),
                None,
            )
            reference_peak = max(range(16), key=lambda band: sum(pair[0].fft[band] for pair in pairs)) if pairs else None
            candidate_peak = max(range(16), key=lambda band: sum(pair[1].fft[band] for pair in pairs)) if pairs else None
            hints.append(
                {
                    "category": "tone_band_mapping",
                    "evidence": (
                        f"{stimulus['frequency_hz']} Hz maps to bin {frequency_bin} and expected band {expected_band}; "
                        f"reference/candidate strongest bands are {reference_peak}/{candidate_peak}"
                    ),
                    "hint": "If the candidate peak band differs from the reference, check FFT sample rate, size and channel mapping.",
                }
            )
        elif stimulus_type in {"white_noise", "pink_noise"} and pairs:
            reference_levels = [sum(pair[0].fft[band] for pair in pairs) / len(pairs) for band in range(16)]
            candidate_levels = [sum(pair[1].fft[band] for pair in pairs) / len(pairs) for band in range(16)]
            def slope(values: list[float]) -> float | None:
                points = [(band, math.log10(value)) for band, value in enumerate(values) if value > 0]
                if len(points) < 2:
                    return None
                mean_x = sum(point[0] for point in points) / len(points)
                mean_y = sum(point[1] for point in points) / len(points)
                denominator = sum((x - mean_x) ** 2 for x, _ in points)
                return sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
            reference_slope = slope(reference_levels)
            candidate_slope = slope(candidate_levels)
            if reference_slope is not None and candidate_slope is not None:
                hints.append(
                    {
                        "category": "noise_band_profile",
                        "evidence": (
                            "log10 output-value profile slope reference/candidate: "
                            f"{reference_slope:.3f}/{candidate_slope:.3f} per band"
                        ),
                        "hint": "A slope difference points to pink correction, microphone profile or frequency-dependent scaling.",
                    }
                )
        elif stimulus_type == "logarithmic_sweep":
            frequency_ratios = [
                candidate.dominant_frequency / reference.dominant_frequency
                for reference, candidate in pairs
                if reference.dominant_frequency > 0 and candidate.dominant_frequency > 0
            ]
            ratio = statistics.median(frequency_ratios) if frequency_ratios else None
            hints.append(
                {
                    "category": "sweep_mapping",
                    "evidence": (
                        f"median candidate/reference dominant-frequency ratio is {ratio:.3f}"
                        if ratio is not None
                        else "dominant-frequency or FFT-band threshold failure"
                    ),
                    "hint": "A frequency-dependent transition shift suggests an FFT sample-rate, size or bin-boundary mismatch.",
                }
            )
        else:
            hints.append(
                {
                    "category": "spectral_mapping",
                    "evidence": (
                        f"threshold failures in {', '.join(sorted(failed_bands))}"
                        if failed_bands
                        else "spectral shape threshold failure"
                    ),
                    "hint": "Compare the failing band profile; check sample rate, FFT size/window, frequency distribution and scaling.",
                }
            )
    if {"volume_raw", "volume_smoothed", "magnitude"} & failed_fields:
        hints.append(
            {
                "category": "gain_or_gating",
                "evidence": "volume or magnitude threshold failure",
                "hint": "Check input level, gain, squelch, AGC mode and dynamics limiter before changing thresholds.",
            }
        )
    if "peak" in failed_fields:
        click_interval = stimulus.get("interval_ms") if isinstance(stimulus, dict) else None
        hints.append(
            {
                "category": "peak_detection",
                "evidence": (
                    f"peak-event threshold failure for a {click_interval} ms click interval"
                    if click_interval is not None
                    else "peak-event threshold failure"
                ),
                "hint": "Compare missed/extra events and timing; check peak threshold, hold-off and packet peak-flag handling.",
            }
        )
    if "dominant_frequency" in failed_fields:
        hints.append(
            {
                "category": "dominant_frequency",
                "evidence": "strong-signal dominant-frequency threshold failure",
                "hint": "Check FFT bin mapping and dominant-frequency weighting for this stimulus and reference profile.",
            }
        )
    return hints


def _windowed_metrics(
    pairs: list[tuple[DecodedPacket, DecodedPacket]], case: dict[str, Any], origin_ns: int | None = None
) -> dict[str, dict[str, dict[str, float]]]:
    definitions = case.get("analysis_windows_ms", [])
    if not definitions:
        return {}
    if not isinstance(definitions, list):
        raise ValueError("analysis_windows_ms must be a list")
    origin = origin_ns if origin_ns is not None else (pairs[0][0].timestamp_ns if pairs else 0)
    results: dict[str, dict[str, dict[str, float]]] = {}
    for index, definition in enumerate(definitions):
        if not isinstance(definition, dict):
            raise ValueError("analysis window must be an object")
        window_id = definition.get("id", f"window-{index + 1}")
        start = definition.get("start")
        end = definition.get("end")
        if not isinstance(window_id, str) or not window_id or not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            raise ValueError("analysis window requires id and increasing numeric start/end")
        selected = [
            pair
            for pair in pairs
            if origin + int(start * 1_000_000) <= pair[0].timestamp_ns <= origin + int(end * 1_000_000)
        ]
        results[window_id] = {
            "volume_raw": _field_metrics(selected, "volume_raw", lambda packet: packet.volume_raw),
            "magnitude": _field_metrics(selected, "magnitude", lambda packet: packet.magnitude),
        }
    return results


def _threshold_failures(metrics: dict[str, dict[str, float | int]], thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for field, limits in thresholds.items():
        if field not in metrics or not isinstance(limits, dict):
            failures.append({"field": field, "reason": "metric or threshold is unavailable"})
            continue
        for statistic, maximum in limits.items():
            observed = metrics[field].get(statistic)
            if not isinstance(maximum, (int, float)) or observed is None:
                failures.append({"field": field, "statistic": statistic, "reason": "invalid threshold"})
            elif observed > maximum:
                failures.append(
                    {"field": field, "statistic": statistic, "observed": observed, "maximum": maximum}
                )
    return failures


def _shape_threshold_failures(scorecard: dict[str, Any], thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for metric, maximum in thresholds.items():
        if metric == "minimum_shape_correlation":
            observed = scorecard.get("mean_shape_correlation")
            if not isinstance(maximum, (int, float)) or observed is None:
                failures.append(
                    {"field": "shape", "statistic": metric, "reason": "temporal correlation is unavailable"}
                )
            elif observed < maximum:
                failures.append(
                    {"field": "shape", "statistic": metric, "observed": observed, "minimum": maximum}
                )
            continue
        observed = scorecard.get(metric)
        if not isinstance(maximum, (int, float)) or observed is None:
            failures.append({"field": "shape", "statistic": metric, "reason": "shape metric is unavailable"})
        elif observed > maximum:
            failures.append(
                {"field": "shape", "statistic": metric, "observed": observed, "maximum": maximum}
            )
    return failures


def _level_threshold_failures(scorecard: dict[str, Any], thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for metric, maximum in thresholds.items():
        observed = scorecard.get(metric)
        if not isinstance(maximum, (int, float)) or observed is None:
            failures.append({"field": "level", "statistic": metric, "reason": "level metric is unavailable"})
        elif observed > maximum:
            failures.append(
                {"field": "level", "statistic": metric, "observed": observed, "maximum": maximum}
            )
    return failures


def compare(reference_path: str | Path, candidate_path: str | Path, case_path: str | Path) -> tuple[dict[str, Any], int]:
    reference_artifact = load_capture(reference_path)
    candidate_artifact = load_capture(candidate_path)
    case = load_case(case_path)
    reference, reference_errors = _decode(reference_artifact)
    candidate, candidate_errors = _decode(candidate_artifact)
    validity_errors = (
        _identity_errors("reference", reference_artifact, reference)
        + _identity_errors("candidate", candidate_artifact, candidate)
        + _settings_errors(reference_artifact, candidate_artifact, case)
    )
    alignment = case.get("alignment", {})
    if not isinstance(alignment, dict):
        raise ValueError("alignment must be an object")
    offset_ms = alignment.get("candidate_offset_ms", 0)
    if not isinstance(offset_ms, (int, float)):
        raise ValueError("candidate_offset_ms must be numeric")
    stimulus = case.get("stimulus", {})
    if not isinstance(stimulus, dict):
        raise ValueError("stimulus must be an object")
    playback_start = reference_artifact["metadata"].get("playback_started_monotonic_ns")
    if not isinstance(playback_start, int):
        playback_start = None
        if stimulus.get("warmup_seconds", 0) > 0:
            validity_errors.append("playback start is unavailable; stimulus warm-up cannot be applied")
    window_origin = playback_start if playback_start is not None else (reference[0].timestamp_ns if reference else 0)
    requested_window = case.get("measurement_window_ms", {})
    if not isinstance(requested_window, dict):
        raise ValueError("measurement_window_ms must be an object")
    warmup_ms = float(stimulus.get("warmup_seconds", 0)) * 1000
    window_start_ms = float(requested_window.get("start", warmup_ms))
    window_end_ms = requested_window.get("end")
    if window_end_ms is None and isinstance(stimulus.get("measurement_seconds"), (int, float)):
        window_end_ms = window_start_ms + float(stimulus["measurement_seconds"]) * 1000
    if window_end_ms is None:
        window_end_ms = (reference[-1].timestamp_ns - window_origin) / 1_000_000 if reference else 0
    window_start_ns = window_origin + int(window_start_ms * 1_000_000)
    window_end_ns = window_origin + int(float(window_end_ms) * 1_000_000)
    alignment_confidence: dict[str, Any] = {
        "method": "declared_offset",
        "confidence": None,
        "ambiguous": False,
    }
    if alignment.get("mode", "manual") == "auto":
        search_range_ms = alignment.get("search_range_ms", 500)
        step_ms = alignment.get("step_ms", 5)
        if (
            not isinstance(search_range_ms, (int, float))
            or search_range_ms <= 0
            or search_range_ms > 10_000
            or not isinstance(step_ms, (int, float))
            or step_ms <= 0
            or 2 * search_range_ms / step_ms > 10_000
        ):
            raise ValueError("automatic alignment search range/step must be positive and bounded")
        offset_ns, alignment_confidence = _estimate_offset(
            reference,
            candidate,
            float(search_range_ms),
            float(step_ms),
            playback_start if playback_start is not None else (reference[0].timestamp_ns if reference else 0),
            window_end_ns,
        )
        offset_ms = offset_ns / 1_000_000
        if alignment_confidence["ambiguous"]:
            validity_errors.append(
                "automatic latency estimate is ambiguous; use an informative stimulus or declare an offset"
            )
    else:
        offset_ns = int(offset_ms * 1_000_000)
    reference_window, candidate_window, window = _window_packets(
        reference, candidate, offset_ns, case, window_origin
    )
    max_distance_ms = alignment.get("maximum_pair_distance_ms", 30)
    if not isinstance(max_distance_ms, (int, float)) or max_distance_ms < 0:
        raise ValueError("maximum_pair_distance_ms must be a non-negative number")
    pairs = _time_pairs(
        reference_window, candidate_window, offset_ns, int(max_distance_ms * 1_000_000)
    )
    coverage = len(pairs) / len(reference_window) if reference_window else 0
    minimum_coverage = case.get("minimum_coverage", 0.8)
    if not isinstance(minimum_coverage, (int, float)) or not 0 <= minimum_coverage <= 1:
        raise ValueError("minimum_coverage must be between zero and one")
    if coverage < minimum_coverage:
        validity_errors.append(f"coverage {coverage:.3f} is below required {minimum_coverage:.3f}")

    metrics: dict[str, dict[str, float | int]] = {}
    for name, extractor in {
        "volume_raw": lambda packet: packet.volume_raw,
        "volume_smoothed": lambda packet: packet.volume_smoothed,
        "magnitude": lambda packet: packet.magnitude,
    }.items():
        metrics[name] = _field_metrics(pairs, name, extractor)
    strong_signal_magnitude = case.get("strong_signal_magnitude", 0)
    if not isinstance(strong_signal_magnitude, (int, float)) or strong_signal_magnitude < 0:
        raise ValueError("strong_signal_magnitude must be a non-negative number")
    frequency_pairs = [
        pair
        for pair in pairs
        if pair[0].magnitude >= strong_signal_magnitude and pair[1].magnitude >= strong_signal_magnitude
    ]
    metrics["dominant_frequency"] = _field_metrics(
        frequency_pairs, "dominant_frequency", lambda packet: packet.dominant_frequency
    )
    for band in range(16):
        metrics[f"fft.{band}"] = _field_metrics(pairs, f"fft.{band}", lambda packet, band=band: packet.fft[band])
    peak_tolerance_ms = case.get("peak_tolerance_ms", 30)
    if not isinstance(peak_tolerance_ms, (int, float)) or peak_tolerance_ms < 0:
        raise ValueError("peak_tolerance_ms must be a non-negative number")
    metrics["peak"] = _peak_metrics(
        reference_window, candidate_window, offset_ns, int(peak_tolerance_ms * 1_000_000)
    )

    band_shape_level = _band_shape_level(pairs, stimulus.get("type"))
    scorecard = dict(band_shape_level["summary"])
    level_thresholds = case.get("level_thresholds", {})
    shape_thresholds = case.get("shape_thresholds", {})
    if not isinstance(level_thresholds, dict) or not isinstance(shape_thresholds, dict):
        raise ValueError("shape_thresholds and level_thresholds must be objects")
    scorecard["level_check"] = (
        "thresholded" if level_thresholds else "not_calibrated"
    )
    reference_model = None
    audio_file = stimulus.get("audio_file")
    if isinstance(audio_file, str):
        audio_path = Path(case_path).resolve().parent / audio_file
        if audio_path.is_file():
            try:
                reference_model = compare_to_models(
                    audio_path,
                    reference_window,
                    candidate_window,
                    reference_artifact["metadata"].get("settings", {}),
                    audio_start_seconds=(
                        window["requested_start_ms"] / 1000
                        if "requested_start_ms" in window
                        else float(stimulus.get("warmup_seconds", 0))
                    ),
                    audio_duration_seconds=(
                        max(0, (window["requested_end_ms"] - window["requested_start_ms"]) / 1000)
                        if "requested_start_ms" in window and "requested_end_ms" in window
                        else (
                            float(stimulus["measurement_seconds"])
                            if isinstance(stimulus.get("measurement_seconds"), (int, float))
                            else None
                        )
                    ),
                )
            except (OSError, TypeError, ValueError, wave.Error) as error:
                reference_model = {"error": str(error)}
    failures = [] if validity_errors else (
        _threshold_failures(metrics, case["thresholds"])
        + _shape_threshold_failures(scorecard, shape_thresholds)
        + _level_threshold_failures(scorecard, level_thresholds)
    )
    verdict = "inconclusive" if validity_errors else "threshold_failure" if failures else "pass"
    exit_code = EXIT_INCONCLUSIVE if validity_errors else EXIT_THRESHOLD_FAILURE if failures else EXIT_PASS
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "case_id": case["id"],
        "verdict": verdict,
        "exit_code": exit_code,
        "provenance": {
            "reference_identity": reference_artifact["metadata"].get("identity"),
            "candidate_identity": candidate_artifact["metadata"].get("identity"),
        },
        "alignment": {
            "candidate_offset_ms": offset_ms,
            "sign_convention": "candidate timestamps are shifted forward by this value",
            **alignment_confidence,
            "maximum_pair_distance_ms": max_distance_ms,
        },
        "window": window,
        "coverage": {
            "reference_packets": len(reference_window),
            "candidate_packets": len(candidate_window),
            "paired_packets": len(pairs),
            "ratio": coverage,
        },
        "quality": {
            "reference": _transport_quality(reference, reference_errors),
            "candidate": _transport_quality(candidate, candidate_errors),
            "reference_decode_errors": reference_errors,
            "candidate_decode_errors": candidate_errors,
        },
        "eligibility": {
            "dominant_frequency_pairs": len(frequency_pairs),
            "strong_signal_magnitude": strong_signal_magnitude,
        },
        "peak_matching": {"tolerance_ms": peak_tolerance_ms},
        "metrics": metrics,
        "band_shape_level": band_shape_level["bands"],
        "scorecard": scorecard,
        "reference_model": reference_model,
        "window_metrics": _windowed_metrics(pairs, case, window_origin),
        "validity_errors": validity_errors,
        "threshold_failures": failures,
        "diagnostics": _diagnostics(
            metrics, failures, case, pairs, reference_artifact["metadata"].get("settings", {})
        ),
    }
    return report, exit_code


def write_report(path: str | Path, report: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
