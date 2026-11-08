"""Lightweight signal fingerprints for comparing candidate band profiles."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Any

from .bands import geq_bands

_PINK_PROFILE = [1.70, 1.71, 1.73, 1.78, 1.68, 1.56, 1.55, 1.63, 1.79, 1.62, 1.80, 2.06, 2.47, 3.35, 6.83, 9.55]


def _read_wav(path: Path) -> tuple[int, list[float]]:
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
            raise ValueError("reference fingerprint model requires uncompressed PCM16 WAV")
        channels = audio.getnchannels()
        rate = audio.getframerate()
        frames = audio.readframes(audio.getnframes())
    values = [value[0] for value in struct.iter_unpack("<h", frames)]
    if channels > 1:
        values = [sum(values[index : index + channels]) / channels for index in range(0, len(values), channels)]
    return rate, [value / 32768 for value in values]


def _mapping_for(
    fft_size: int,
    actual_rate: int,
    mapping: str,
    interpreted_rate: int | None = None,
    frequency_distribution: int = 0,
) -> list[tuple[int, int]]:
    if mapping == "linear_band_split":
        maximum_bin = min(fft_size // 2 - 1, 215)
        width = (maximum_bin - 1 + 1) / 16
        return [
            (max(1, math.floor(1 + band * width)), min(maximum_bin, math.ceil(1 + (band + 1) * width) - 1))
            for band in range(16)
        ]
    source_bands = geq_bands(frequency_distribution)
    rate_ratio = actual_rate / interpreted_rate if interpreted_rate else 1.0
    max_bin = fft_size // 2 - 1
    mapped_bands = []
    for low, high, _ in source_bands:
        mapped_low = max(1, round(low * rate_ratio))
        mapped_high = min(max_bin, round(high * rate_ratio))
        mapped_bands.append(
            (mapped_low, mapped_high) if mapped_low <= mapped_high else (max_bin + 1, max_bin)
        )
    return mapped_bands


def _fingerprint(
    samples: list[float],
    sample_rate: int,
    *,
    fft_size: int = 512,
    mapping: str = "reference",
    interpreted_rate: int | None = None,
    frequency_distribution: int = 0,
    use_input_filter: bool = False,
    pink_correction: bool = True,
    scaling: str = "sqrt",
    input_gain: float = 1.5625,
    pink_profile: list[float] | None = None,
    window_correction: float = 1.0,
    average_mode: str = "mean",
    agc_post_gain: float = 1.0,
    pcm_to_fft_scale: float = 1.0,
    frame_stride: int = 1,
    normalize_output: bool = True,
) -> list[float]:
    hop = max(1, round(sample_rate * 0.02)) * max(1, frame_stride)
    if mapping == "linear_band_split":
        bands = [(low, high, 1.0) for low, high in _mapping_for(
            fft_size, sample_rate, mapping, interpreted_rate, frequency_distribution
        )]
    else:
        bands = geq_bands(frequency_distribution, use_input_filter)
    window = [
        0.35875
        - 0.48829 * math.cos(2 * math.pi * index / (fft_size - 1))
        + 0.14128 * math.cos(4 * math.pi * index / (fft_size - 1))
        - 0.01168 * math.cos(6 * math.pi * index / (fft_size - 1))
        for index in range(fft_size)
    ]
    maximum_bin = min(fft_size // 2 - 1, max(high for _, high, _ in bands))
    bit_reversed = []
    for index in range(fft_size):
        value = index
        reversed_value = 0
        for _ in range(fft_size.bit_length() - 1):
            reversed_value = (reversed_value << 1) | (value & 1)
            value >>= 1
        bit_reversed.append(reversed_value)
    butterflies = []
    width = 2
    while width <= fft_size:
        butterflies.append(
            [complex(math.cos(-2 * math.pi * index / width), math.sin(-2 * math.pi * index / width)) for index in range(width // 2)]
        )
        width *= 2
    accumulated = [0.0] * 16
    frame_count = 0
    selected_pink_profile = pink_profile or _PINK_PROFILE
    scale_mode = {"none": 0, "log": 1, "linear": 2, "sqrt": 3}.get(scaling, scaling)
    for start in range(0, len(samples) - fft_size + 1, hop):
        spectrum = [
            complex(samples[start + index] * pcm_to_fft_scale * window[index], 0)
            for index in range(fft_size)
        ]
        spectrum = [spectrum[index] for index in bit_reversed]
        width = 2
        for twiddles in butterflies:
            half_width = width // 2
            for block in range(0, fft_size, width):
                for index, twiddle in enumerate(twiddles):
                    even = spectrum[block + index]
                    odd = twiddle * spectrum[block + index + half_width]
                    spectrum[block + index] = even + odd
                    spectrum[block + index + half_width] = even - odd
            width *= 2
        magnitudes = [abs(value) for value in spectrum[: maximum_bin + 1]]
        for band, (low, high, band_multiplier) in enumerate(bands):
            lower = min(low, maximum_bin)
            upper = min(high, maximum_bin)
            bin_values = magnitudes[lower : upper + 1] if upper >= lower else []
            if bin_values:
                average = (
                    math.sqrt(sum(value * value for value in bin_values) / len(bin_values))
                    if average_mode == "rms"
                    else sum(bin_values) / len(bin_values)
                )
            else:
                average = 0.0
            profile_factor = selected_pink_profile[band] if pink_correction else 1.0
            mapped = average / 16 * window_correction * band_multiplier * profile_factor
            if scale_mode:
                mapped *= 0.40
            mapped *= input_gain
            accumulated[band] += _scale_fft_value(
                mapped, band, scale_mode, agc_post_gain=agc_post_gain
            )
        frame_count += 1
    if frame_count == 0:
        return [0.0] * 16
    accumulated = [value / frame_count for value in accumulated]
    if not normalize_output:
        return accumulated
    total = sum(accumulated)
    return [value / total for value in accumulated] if total > 0 else [0.0] * 16


def _scale_fft_value(value: float, band: int, mode: int | str, *, agc_post_gain: float = 1.0) -> int:
    """Apply MoonModules' documented FFT output transfer function to one channel."""
    if mode == 1:
        current = value * 0.42 - 8.0
        current = math.log(current) if current > 1 else 0.0
        current *= 0.85 + band / 18.0
        current = current * 255 / math.log(256)
    elif mode == 2:
        current = value * 0.30 - 2.0
        if current < 1:
            current = 0.0
        current *= 0.85 + band / 1.8
    elif mode == 3:
        current = value * 0.38 - 6.0
        current = math.sqrt(current) if current > 1 else 0.0
        current *= 0.85 + band / 4.5
        current = current * 255 / 16
    else:
        current = value - 2.0
    current *= agc_post_gain
    return max(0, min(255, int(current + 0.5)))


def _normalize_profile(levels: list[float]) -> list[float]:
    total = sum(levels)
    return [value / total for value in levels] if total > 0 else [0.0] * 16


def _observed_levels(packets: list[Any]) -> list[float]:
    if not packets:
        return [0.0] * 16
    return [sum(float(packet.fft[band]) for packet in packets) / len(packets) for band in range(16)]


def _is_degenerate(levels: list[float]) -> bool:
    return not any(level > 0 for level in levels) or all(level >= 255 for level in levels)


def _rank_hypotheses(
    observed: list[float], hypotheses: dict[str, tuple[list[float], list[float]]]
) -> list[dict[str, Any]]:
    ranked = []
    for name, (profile, levels) in hypotheses.items():
        if _is_degenerate(levels):
            ranked.append({"model": name, "status": "degenerate", "normalized_profile_mae": None})
        else:
            ranked.append(
                {
                    "model": name,
                    "status": "ranked",
                    "normalized_profile_mae": sum(
                        abs(expected - actual) for expected, actual in zip(profile, observed)
                    )
                    / 16,
                }
            )
    return sorted(
        ranked,
        key=lambda item: (
            item["status"] == "degenerate",
            item["normalized_profile_mae"] if item["normalized_profile_mae"] is not None else math.inf,
        ),
    )


def _fit_input_scale(
    samples: list[float],
    sample_rate: int,
    reference_packets: list[Any],
    options: dict[str, Any],
    base_input_gain: float,
    base_pcm_to_fft_scale: float,
) -> tuple[float, float, dict[str, Any]]:
    target = _observed_levels(reference_packets)
    if not reference_packets or _is_degenerate(target):
        return base_input_gain, base_pcm_to_fft_scale, {
            "status": "degenerate_reference",
            "reason": "reference capture has all-zero or saturated FFT bands",
        }

    def evaluate(log_correction: float) -> tuple[float, float, float]:
        correction = 10**log_correction
        correction_root = math.sqrt(correction)
        fitted_gain = base_input_gain * correction_root
        fitted_pcm_scale = base_pcm_to_fft_scale * correction_root
        levels = _fingerprint(
            samples,
            sample_rate,
            **{
                **options,
                "input_gain": fitted_gain,
                "pcm_to_fft_scale": fitted_pcm_scale,
                "frame_stride": 4,
                "normalize_output": False,
            },
        )
        error = sum(abs(actual - expected) for actual, expected in zip(levels, target)) / 16
        if _is_degenerate(levels):
            error += 256
        return error, fitted_gain, fitted_pcm_scale

    coarse_logs = [index / 2 for index in range(-12, 13)]
    coarse = [(evaluate(value)[0], value) for value in coarse_logs]
    best_log = min(coarse, key=lambda result: result[0])[1]
    fine_logs = [best_log - 0.5 + index * 0.05 for index in range(21)]
    best_error, best_log = min((evaluate(value)[0], value) for value in fine_logs)
    _, fitted_gain, fitted_pcm_scale = evaluate(best_log)
    return fitted_gain, fitted_pcm_scale, {
        "status": "fitted",
        "objective": "mean_absolute_fft_output_value_error",
        "mean_absolute_error": best_error,
        "multiplicative_correction": 10**best_log,
        "pcm_to_fft_scale": fitted_pcm_scale,
        "input_gain": fitted_gain,
        "note": "The shared correction is split equally in log space between input gain and PCM scale.",
    }


def compare_to_models(
    audio_path: str | Path,
    reference_packets: list[Any],
    candidate_packets: list[Any],
    settings: dict[str, Any] | None = None,
    *,
    audio_start_seconds: float = 0,
    audio_duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Compare capture band profiles against the configured DSP fingerprint."""
    settings = settings or {}
    sample_rate, samples = _read_wav(Path(audio_path))
    start_sample = max(0, round(audio_start_seconds * sample_rate))
    end_sample = (
        len(samples)
        if audio_duration_seconds is None
        else min(len(samples), start_sample + max(0, round(audio_duration_seconds * sample_rate)))
    )
    samples = samples[start_sample:end_sample]
    configured_rate = settings.get("sample_rate_hz", sample_rate)
    if not isinstance(configured_rate, int) or configured_rate <= 0:
        configured_rate = sample_rate
    configured_fft_size = settings.get("fft_size", 512)
    if not isinstance(configured_fft_size, int) or configured_fft_size < 32 or configured_fft_size & (configured_fft_size - 1):
        configured_fft_size = 512
    frequency_distribution = settings.get("frequency_distribution", 0)
    if frequency_distribution not in (0, 1):
        frequency_distribution = 0
    use_input_filter = bool(settings.get("use_input_filter", False))
    pink_index = settings.get("pink_index", 0)
    pink_profile_setting = settings.get("pink_profile")
    pink_profile_known = pink_index == 0
    if isinstance(pink_profile_setting, list) and len(pink_profile_setting) == 16 and all(
        isinstance(value, (int, float)) and value > 0 for value in pink_profile_setting
    ):
        pink_profile = [float(value) for value in pink_profile_setting]
        pink_profile_known = True
    else:
        pink_profile = _PINK_PROFILE

    scaling_setting = settings.get("fft_scaling", 3)
    scaling_modes = {
        0: 0, 1: 1, 2: 2, 3: 3,
        "none": 0, "log": 1, "linear": 2, "sqrt": 3,
    }
    scaling_mode = scaling_modes.get(scaling_setting, 3)
    manual_gain = (
        float(settings.get("sample_gain", 60)) / 40
        * float(settings.get("input_level", 128)) / 128
        + 1 / 16
    )
    agc_enabled = settings.get("agc_mode", 0) not in (0, "off", "none", False)
    input_level = float(settings.get("input_level", 128))
    agc_post_gain = input_level / 128
    if agc_post_gain < 1:
        agc_post_gain = (agc_post_gain - 1) * 0.8 + 1
    input_gain = float(settings.get("agc_fft_gain", 1.0)) if agc_enabled else manual_gain
    average_mode = settings.get("average_mode", "mean")
    if average_mode not in ("mean", "rms"):
        average_mode = "mean"
    window_correction = float(settings.get("window_correction", 1.0))
    pcm_to_fft_scale = float(settings.get("pcm_to_fft_scale", 1.0))
    fft_window = settings.get("fft_window")
    window_supported = fft_window in (
        0,
        "blackman_harris",
        "blackman-harris",
        "blackmanharris",
    )

    if not math.isfinite(pcm_to_fft_scale) or pcm_to_fft_scale <= 0:
        pcm_to_fft_scale = 1.0
    options = {
        "fft_size": configured_fft_size,
        "interpreted_rate": configured_rate,
        "frequency_distribution": frequency_distribution,
        "use_input_filter": use_input_filter,
        "pink_profile": pink_profile,
        "window_correction": window_correction,
        "average_mode": average_mode,
        "scaling": scaling_mode,
        "agc_post_gain": agc_post_gain if agc_enabled else 1.0,
    }
    fitted_gain, fitted_pcm_scale, scale_fit = _fit_input_scale(
        samples, sample_rate, reference_packets, options, input_gain, pcm_to_fft_scale
    )
    options.update(input_gain=fitted_gain, pcm_to_fft_scale=fitted_pcm_scale)

    def fingerprint_raw(**overrides: Any) -> list[float]:
        hypothesis_options = {**options, **overrides}
        return _fingerprint(samples, sample_rate, normalize_output=False, **hypothesis_options)

    def fingerprint(**overrides: Any) -> tuple[list[float], list[float]]:
        levels = fingerprint_raw(**overrides)
        return _normalize_profile(levels), levels

    wrong_rate = 18_000 if configured_rate != 18_000 else 22_050
    wrong_fft_size = 256 if configured_fft_size != 256 else 1024
    hypotheses = {
        "reference_fingerprint": fingerprint(),
        "linear_band_split": fingerprint(mapping="linear_band_split"),
        f"wrong_sample_rate_{wrong_rate}_hz": fingerprint(interpreted_rate=wrong_rate),
        f"wrong_fft_size_{wrong_fft_size}": fingerprint(fft_size=wrong_fft_size),
        "no_pink_correction": fingerprint(pink_correction=False),
    }
    for mutant_name, mutant_scale in (
        ("wrong_scaling_none", 0),
        ("wrong_scaling_log", 1),
        ("wrong_scaling_linear", 2),
        ("wrong_scaling_sqrt", 3),
    ):
        if mutant_scale != scaling_mode:
            hypotheses[mutant_name] = fingerprint(scaling=mutant_scale)

    reference_levels = _observed_levels(reference_packets)
    candidate_levels = _observed_levels(candidate_packets)
    reference_ranking = _rank_hypotheses(_normalize_profile(reference_levels), hypotheses)
    candidate_ranking = _rank_hypotheses(_normalize_profile(candidate_levels), hypotheses)
    valid_reference_ranking = [item for item in reference_ranking if item["status"] == "ranked"]
    valid_candidate_ranking = [item for item in candidate_ranking if item["status"] == "ranked"]
    reference_best = valid_reference_ranking[0]["model"] if valid_reference_ranking else None
    reference_tied = len(valid_reference_ranking) > 1 and math.isclose(
        valid_reference_ranking[0]["normalized_profile_mae"],
        valid_reference_ranking[1]["normalized_profile_mae"],
        abs_tol=1e-6,
    )
    unmodeled_settings = []
    for key in (
        "sample_rate_hz",
        "fft_size",
        "frequency_distribution",
        "fft_scaling",
        "average_mode",
        "sample_gain",
        "input_level",
        "agc_mode",
        "pink_index",
        "fft_window",
        "use_input_filter",
        "dynamics_limiter",
        "fastpath",
        "sliding_window",
    ):
        if key not in settings:
            unmodeled_settings.append(key)
    if not window_supported:
        unmodeled_settings.append("non-Blackman-Harris FFT window")
    if settings.get("use_input_filter") not in (False, 0):
        unmodeled_settings.append("input filtering")
    if settings.get("dynamics_limiter") not in (False, 0):
        unmodeled_settings.append("dynamics limiter")
    if settings.get("fastpath") not in (False, 0) or settings.get("sliding_window") not in (False, 0, None):
        unmodeled_settings.append("fast-path smoothing")
    if agc_enabled:
        unmodeled_settings.append("AGC dynamics")
    if scaling_setting not in scaling_modes:
        unmodeled_settings.append("unknown FFT scaling mode")
    if not pink_profile_known:
        unmodeled_settings.append("unknown pink profile")
    settings_modelable = not unmodeled_settings
    reference_degenerate = _is_degenerate(reference_levels)
    trustworthy = (
        settings_modelable
        and not reference_degenerate
        and reference_best == "reference_fingerprint"
        and not reference_tied
    )
    candidate_degenerate = _is_degenerate(candidate_levels)
    candidate_tied = len(valid_candidate_ranking) > 1 and math.isclose(
        valid_candidate_ranking[0]["normalized_profile_mae"],
        valid_candidate_ranking[1]["normalized_profile_mae"],
        abs_tol=1e-6,
    )
    if reference_degenerate:
        reference_reason = "reference capture does not validate model assumptions: FFT profile is degenerate (all-zero or saturated)"
    elif not valid_reference_ranking:
        reference_reason = "reference capture does not validate model assumptions: all model fingerprints are degenerate (all-zero or saturated)"
    elif reference_tied:
        reference_reason = "reference capture does not validate model assumptions: ranking is tied between model hypotheses"
    elif reference_best != "reference_fingerprint":
        reference_reason = (
            f"reference capture does not validate model assumptions: best matches {reference_best}, "
            "not reference_fingerprint"
        )
    elif unmodeled_settings:
        reference_reason = (
            "reference capture does not validate the model assumptions; unmodeled settings: "
            + ", ".join(unmodeled_settings)
        )
    else:
        reference_reason = None
    return {
        "audio_sample_rate_hz": sample_rate,
        "reference_settings": settings,
        "method": "normalized_fft_band_profile_mae",
        "scope": (
            "diagnostic approximation; Blackman-Harris window, GEQ map, averaging and documented output scaling; "
            "does not model input filtering, AGC dynamics, limiter state, or fast-path smoothing"
        ),
        "reference_capture_ranking": {
            "best_match": reference_best,
            "ranking_is_trustworthy": trustworthy,
            "status": "degenerate" if reference_degenerate or not valid_reference_ranking else "ranked",
            "ranked_hypotheses": reference_ranking,
            "reason": None if trustworthy else reference_reason,
        },
        "candidate_capture_ranking": {
            "best_match": (
                valid_candidate_ranking[0]["model"]
                if trustworthy and valid_candidate_ranking and not candidate_tied and not candidate_degenerate
                else None
            ),
            "ambiguous_best_match": candidate_tied and not candidate_degenerate,
            "status": "degenerate" if candidate_degenerate else "ranked",
            "ranked_hypotheses": candidate_ranking,
            "ranking_is_trustworthy": trustworthy,
        },
        "input_scale_fit": scale_fit,
        "configuration_known": {
            "pink_profile": pink_profile_known,
            "fft_scaling": scaling_setting in scaling_modes,
            "average_mode": "average_mode" in settings,
            "window_correction": window_correction,
            "fft_window_supported": window_supported,
            "input_gain": fitted_gain,
            "pcm_to_fft_scale": fitted_pcm_scale,
            "agc_fft_gain": "agc_fft_gain" in settings if agc_enabled else None,
            "unmodeled_settings": unmodeled_settings,
        },
        "temporal_behavior_hypotheses": "not ranked; behavior scores are not validated against the reference capture",
    }
