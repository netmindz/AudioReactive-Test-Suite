"""Lightweight signal fingerprints for comparing candidate band profiles."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Any


_REFERENCE_BANDS = [
    (1, 1), (2, 2), (3, 4), (5, 6), (7, 9), (10, 12), (13, 18), (19, 25),
    (26, 32), (33, 43), (44, 55), (56, 69), (70, 85), (86, 103), (104, 164), (165, 215),
]
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
    if frequency_distribution == 1:
        source_bands = [
            (1, 1), (2, 2), (3, 3), (4, 4), (5, 6), (7, 8), (9, 10), (11, 13),
            (14, 18), (19, 25), (26, 36), (37, 45), (46, 66), (67, 97), (98, 164), (165, 215),
        ]
    else:
        source_bands = _REFERENCE_BANDS
    rate_ratio = actual_rate / interpreted_rate if interpreted_rate else 1.0
    max_bin = fft_size // 2 - 1
    mapped_bands = []
    for low, high in source_bands:
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
    pink_correction: bool = True,
    scaling: str = "sqrt",
) -> list[float]:
    hop = max(1, round(sample_rate * 0.02))
    bands = _mapping_for(fft_size, sample_rate, mapping, interpreted_rate, frequency_distribution)
    window = [
        0.35875
        - 0.48829 * math.cos(2 * math.pi * index / (fft_size - 1))
        + 0.14128 * math.cos(4 * math.pi * index / (fft_size - 1))
        - 0.01168 * math.cos(6 * math.pi * index / (fft_size - 1))
        for index in range(fft_size)
    ]
    maximum_bin = min(fft_size // 2 - 1, max(high for _, high in bands))
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
    for start in range(0, len(samples) - fft_size + 1, hop):
        spectrum = [complex(samples[start + index] * window[index], 0) for index in range(fft_size)]
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
        for band, (low, high) in enumerate(bands):
            lower = min(low, maximum_bin)
            upper = min(high, maximum_bin)
            value = (
                sum(magnitudes[lower : upper + 1]) / (upper - lower + 1)
                if upper >= lower
                else 0.0
            )
            if pink_correction:
                value *= _PINK_PROFILE[band]
            if scaling == "sqrt":
                value = math.sqrt(value)
            elif scaling == "log":
                value = math.log1p(value)
            accumulated[band] += value
        frame_count += 1
    if frame_count == 0:
        return [0.0] * 16
    accumulated = [value / frame_count for value in accumulated]
    total = sum(accumulated)
    return [value / total for value in accumulated] if total > 0 else [0.0] * 16


def _rms_envelope(samples: list[float], sample_rate: int, frame_size: int) -> tuple[float, list[float]]:
    hop = max(1, round(sample_rate * 0.02))
    envelope = []
    for start in range(0, len(samples) - frame_size + 1, hop):
        frame = samples[start : start + frame_size]
        envelope.append(math.sqrt(sum(value * value for value in frame) / len(frame)))
    return hop / sample_rate, envelope


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


def _stateful_mutant_scores(
    samples: list[float],
    sample_rate: int,
    frame_size: int,
    candidate_packets: list[Any],
    playback_start_ns: int | None,
    candidate_offset_ns: int,
) -> dict[str, Any]:
    if playback_start_ns is None or not candidate_packets:
        return {"available": False, "reason": "playback start and candidate packets are required"}
    frame_seconds, envelope = _rms_envelope(samples, sample_rate, frame_size)
    if len(envelope) < 3:
        return {"available": False, "reason": "audio is too short for temporal model features"}

    def audio_value(packet: Any) -> float | None:
        elapsed = (packet.timestamp_ns + candidate_offset_ns - playback_start_ns) / 1_000_000_000
        position = elapsed / frame_seconds
        index = math.floor(position)
        if index < 0 or index + 1 >= len(envelope):
            return None
        fraction = position - index
        return envelope[index] + fraction * (envelope[index + 1] - envelope[index])

    aligned = [(packet, audio_value(packet)) for packet in candidate_packets]
    aligned = [(packet, value) for packet, value in aligned if value is not None]
    audio_rms = [float(value) for _, value in aligned]
    volume = [float(packet.volume_raw) for packet, _ in aligned]
    magnitude = [float(packet.magnitude) for packet, _ in aligned]
    volume_correlation = _correlation(audio_rms, volume)
    magnitude_correlation = _correlation(audio_rms, magnitude)

    maximum = max(envelope)
    threshold = maximum * 0.5
    predicted_events = [
        index * frame_seconds
        for index in range(1, len(envelope))
        if envelope[index] >= threshold > envelope[index - 1]
    ]
    candidate_events = []
    previously_high = False
    for packet in candidate_packets:
        if packet.peak and not previously_high:
            candidate_events.append(
                (packet.timestamp_ns + candidate_offset_ns - playback_start_ns) / 1_000_000_000
            )
        previously_high = packet.peak
    remaining = list(candidate_events)
    matched = 0
    for predicted in predicted_events:
        nearest = min(range(len(remaining)), key=lambda index: abs(remaining[index] - predicted), default=None)
        if nearest is not None and abs(remaining[nearest] - predicted) <= 0.1:
            matched += 1
            remaining.pop(nearest)
    precision = matched / len(candidate_events) if candidate_events else 0.0
    recall = matched / len(predicted_events) if predicted_events else 0.0
    peak_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    stateful = {
        "available": True,
        "feature_samples": len(aligned),
        "magnitude_as_amplitude": {
            "audio_envelope_correlation": magnitude_correlation,
            "method": "correlation of packet magnitude with input RMS envelope",
        },
        "volume_spike_peak_detector": {
            "event_f1": peak_f1,
            "predicted_events": len(predicted_events),
            "candidate_events": len(candidate_events),
            "matched_events": matched,
            "method": "RMS envelope half-maximum rising crossings, 100 ms event tolerance",
        },
        "no_agc": {
            "volume_envelope_correlation": volume_correlation,
            "method": "correlation of packet raw volume with unprocessed input RMS envelope",
        },
        "caveat": "These temporal fingerprints are hypotheses, not a calibrated or bit-accurate AGC/peak model.",
    }
    stateful_scores = [
        ("magnitude_as_amplitude", magnitude_correlation),
        ("volume_spike_peak_detector", peak_f1),
        ("no_agc", volume_correlation),
    ]
    stateful["ranked_hypotheses"] = [
        {"model": name, "score": score}
        for name, score in sorted(
            ((name, score) for name, score in stateful_scores if score is not None),
            key=lambda item: item[1],
            reverse=True,
        )
    ]
    stateful["best_match"] = (
        stateful["ranked_hypotheses"][0]["model"] if stateful["ranked_hypotheses"] else None
    )
    return stateful


def compare_to_models(
    audio_path: str | Path,
    candidate_packets: list[Any],
    settings: dict[str, Any] | None = None,
    playback_start_ns: int | None = None,
    candidate_offset_ns: int = 0,
) -> dict[str, Any]:
    """Rank simplified DSP hypotheses by normalized FFT-band profile similarity."""
    settings = settings or {}
    sample_rate, samples = _read_wav(Path(audio_path))
    configured_rate = settings.get("sample_rate_hz", sample_rate)
    if not isinstance(configured_rate, int) or configured_rate <= 0:
        configured_rate = sample_rate
    configured_fft_size = settings.get("fft_size", 512)
    if not isinstance(configured_fft_size, int) or configured_fft_size < 32 or configured_fft_size & (configured_fft_size - 1):
        configured_fft_size = 512
    frequency_distribution = settings.get("frequency_distribution", 0)
    if frequency_distribution not in (0, 1):
        frequency_distribution = 0
    reference = _fingerprint(
        samples,
        sample_rate,
        fft_size=configured_fft_size,
        interpreted_rate=configured_rate,
        frequency_distribution=frequency_distribution,
    )
    wrong_rate = 18_000 if configured_rate != 18_000 else 22_050
    wrong_fft_size = 256 if configured_fft_size != 256 else 1024
    hypotheses = {
        "reference_fingerprint": reference,
        "linear_band_split": _fingerprint(
            samples,
            sample_rate,
            fft_size=configured_fft_size,
            mapping="linear_band_split",
            interpreted_rate=configured_rate,
        ),
        f"wrong_sample_rate_{wrong_rate}_hz": _fingerprint(
            samples,
            sample_rate,
            fft_size=configured_fft_size,
            interpreted_rate=wrong_rate,
            frequency_distribution=frequency_distribution,
        ),
        f"wrong_fft_size_{wrong_fft_size}": _fingerprint(
            samples,
            sample_rate,
            fft_size=wrong_fft_size,
            interpreted_rate=configured_rate,
            frequency_distribution=frequency_distribution,
        ),
        "no_pink_correction": _fingerprint(
            samples,
            sample_rate,
            fft_size=configured_fft_size,
            interpreted_rate=configured_rate,
            frequency_distribution=frequency_distribution,
            pink_correction=False,
        ),
        "wrong_scaling_linear": _fingerprint(
            samples,
            sample_rate,
            fft_size=configured_fft_size,
            interpreted_rate=configured_rate,
            frequency_distribution=frequency_distribution,
            scaling="linear",
        ),
        "wrong_scaling_log": _fingerprint(
            samples,
            sample_rate,
            fft_size=configured_fft_size,
            interpreted_rate=configured_rate,
            frequency_distribution=frequency_distribution,
            scaling="log",
        ),
        "wrong_scaling_none": _fingerprint(
            samples,
            sample_rate,
            fft_size=configured_fft_size,
            interpreted_rate=configured_rate,
            frequency_distribution=frequency_distribution,
            scaling="none",
        ),
    }
    if candidate_packets:
        levels = [
            sum(float(packet.fft[band]) for packet in candidate_packets) / len(candidate_packets)
            for band in range(16)
        ]
        total = sum(levels)
        observed = [value / total for value in levels] if total > 0 else [0.0] * 16
    else:
        observed = [0.0] * 16
    ranked = sorted(
        (
            {
                "model": name,
                "normalized_profile_mae": sum(abs(expected - actual) for expected, actual in zip(profile, observed)) / 16,
            }
            for name, profile in hypotheses.items()
        ),
        key=lambda item: item["normalized_profile_mae"],
    )
    closest = ranked[0]["normalized_profile_mae"] if ranked else None
    tied = [
        item["model"]
        for item in ranked
        if closest is not None and abs(item["normalized_profile_mae"] - closest) <= 1e-6
    ]
    return {
        "audio_sample_rate_hz": sample_rate,
        "candidate_settings": settings,
        "method": "normalized_fft_band_profile_mae",
        "scope": (
            "diagnostic fingerprint only; simplified Blackman-Harris FFT and default pink profile, "
            "not a bit-accurate MoonModules reference"
        ),
        "best_match": ranked[0]["model"] if ranked and len(tied) == 1 else None,
        "ambiguous_best_match": len(tied) != 1,
        "ranked_hypotheses": ranked,
        "stateful_mutant_scores": _stateful_mutant_scores(
            samples,
            sample_rate,
            configured_fft_size,
            candidate_packets,
            playback_start_ns,
            candidate_offset_ns,
        ),
    }
