"""Time-based capture comparison and verdict generation."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Iterable

from .capture import load_capture
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
    if not isinstance(case.get("thresholds"), dict) or not case["thresholds"]:
        raise ValueError("case requires at least one threshold")
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
) -> tuple[list[DecodedPacket], list[DecodedPacket], dict[str, Any]]:
    if not reference or not candidate:
        return [], [], {"reason": "one stream has no valid packets"}

    candidate_times = [packet.timestamp_ns + offset_ns for packet in candidate]
    overlap_start = max(reference[0].timestamp_ns, candidate_times[0])
    overlap_end = min(reference[-1].timestamp_ns, candidate_times[-1])
    window = case.get("measurement_window_ms", {})
    if not isinstance(window, dict):
        raise ValueError("measurement_window_ms must be an object")
    origin = reference[0].timestamp_ns
    start = max(overlap_start, origin + int(window.get("start", 0) * 1_000_000))
    end = min(overlap_end, origin + int(window.get("end", (reference[-1].timestamp_ns - origin) / 1_000_000) * 1_000_000))
    selected_reference = [packet for packet in reference if start <= packet.timestamp_ns <= end]
    selected_candidate = [
        packet for packet, shifted_time in zip(candidate, candidate_times) if start <= shifted_time <= end
    ]
    return selected_reference, selected_candidate, {
        "start_ns": start,
        "end_ns": end,
        "overlap_duration_ms": max(0, end - start) / 1_000_000,
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
    metrics: dict[str, dict[str, float | int]], failures: list[dict[str, Any]], case: dict[str, Any]
) -> list[dict[str, str]]:
    failed_fields = {failure["field"] for failure in failures if "field" in failure}
    hints: list[dict[str, str]] = []
    failed_bands = [field for field in failed_fields if field.startswith("fft.")]
    if failed_bands:
        hints.append(
            {
                "category": "spectral_mapping",
                "evidence": f"threshold failures in {', '.join(sorted(failed_bands))}",
                "hint": "Check sample rate, FFT size/window, frequency distribution, pink profile and FFT scaling.",
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
        hints.append(
            {
                "category": "peak_detection",
                "evidence": "peak-event threshold failure",
                "hint": "Check peak threshold, hold-off and packet peak-flag behavior against the configured click stimulus.",
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
    pairs: list[tuple[DecodedPacket, DecodedPacket]], case: dict[str, Any]
) -> dict[str, dict[str, dict[str, float]]]:
    definitions = case.get("analysis_windows_ms", [])
    if not definitions:
        return {}
    if not isinstance(definitions, list):
        raise ValueError("analysis_windows_ms must be a list")
    origin = pairs[0][0].timestamp_ns if pairs else 0
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
    offset_ns = int(offset_ms * 1_000_000)
    reference_window, candidate_window, window = _window_packets(reference, candidate, offset_ns, case)
    max_distance_ms = alignment.get("maximum_pair_distance_ms", 30)
    if not isinstance(max_distance_ms, (int, float)) or max_distance_ms < 0:
        raise ValueError("maximum_pair_distance_ms must be a non-negative number")
    pairs = _nearest_pairs(reference_window, candidate_window, offset_ns, int(max_distance_ms * 1_000_000))
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

    failures = [] if validity_errors else _threshold_failures(metrics, case["thresholds"])
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
        "window_metrics": _windowed_metrics(pairs, case),
        "validity_errors": validity_errors,
        "threshold_failures": failures,
        "diagnostics": _diagnostics(metrics, failures, case),
    }
    return report, exit_code


def write_report(path: str | Path, report: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
