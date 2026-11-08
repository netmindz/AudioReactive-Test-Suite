"""Deterministic PCM WAV fixtures for AudioReactive comparison runs."""

from __future__ import annotations

import json
import math
import random
import wave
from pathlib import Path
from typing import Callable


SAMPLE_RATE_HZ = 22_050
PCM_MAX = 32_767


def _sample_count(duration_seconds: float) -> int:
    return round(duration_seconds * SAMPLE_RATE_HZ)


def _sine(frequency_hz: float, amplitude: float) -> Callable[[int, int], float]:
    return lambda index, _: amplitude * math.sin(2 * math.pi * frequency_hz * index / SAMPLE_RATE_HZ)


def _multitone(frequencies_hz: list[float], amplitude: float) -> Callable[[int, int], float]:
    return lambda index, _: sum(
        amplitude * math.sin(2 * math.pi * frequency * index / SAMPLE_RATE_HZ) for frequency in frequencies_hz
    ) / len(frequencies_hz)


def _sweep(start_hz: float, end_hz: float, duration_seconds: float, amplitude: float) -> Callable[[int, int], float]:
    logarithmic_ratio = math.log(end_hz / start_hz)

    def sample(index: int, total: int) -> float:
        time_seconds = index / SAMPLE_RATE_HZ
        phase = 2 * math.pi * start_hz * (math.exp(logarithmic_ratio * time_seconds / duration_seconds) - 1)
        return amplitude * math.sin(phase / (logarithmic_ratio / duration_seconds))

    return sample


def _click_train(interval_seconds: float, amplitude: float) -> Callable[[int, int], float]:
    click_samples = round(0.01 * SAMPLE_RATE_HZ)
    interval_samples = round(interval_seconds * SAMPLE_RATE_HZ)

    def sample(index: int, _: int) -> float:
        within_click = index % interval_samples
        if within_click >= click_samples:
            return 0
        envelope = math.exp(-7 * within_click / click_samples)
        return amplitude * envelope * math.sin(2 * math.pi * 1_000 * within_click / SAMPLE_RATE_HZ)

    return sample


def _amplitude_modulated(carrier_hz: float, modulation_hz: float, amplitude: float) -> Callable[[int, int], float]:
    def sample(index: int, _: int) -> float:
        time_seconds = index / SAMPLE_RATE_HZ
        envelope = 0.1 + 0.9 * (0.5 + 0.5 * math.sin(2 * math.pi * modulation_hz * time_seconds))
        return amplitude * envelope * math.sin(2 * math.pi * carrier_hz * time_seconds)

    return sample


def _white_noise(seed: int, amplitude: float) -> Callable[[int, int], float]:
    generator = random.Random(seed)
    return lambda _index, _total: amplitude * generator.uniform(-1, 1)


def _pink_noise(seed: int, amplitude: float) -> Callable[[int, int], float]:
    generator = random.Random(seed)
    b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0

    def sample(_index: int, _total: int) -> float:
        nonlocal b0, b1, b2, b3, b4, b5, b6
        white = generator.uniform(-1, 1)
        b0 = 0.99886 * b0 + white * 0.0555179
        b1 = 0.99332 * b1 + white * 0.0750759
        b2 = 0.96900 * b2 + white * 0.1538520
        b3 = 0.86650 * b3 + white * 0.3104856
        b4 = 0.55000 * b4 + white * 0.5329522
        b5 = -0.7616 * b5 - white * 0.0168980
        pink = b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362
        b6 = white * 0.115926
        return amplitude * pink / 3.5

    return sample


def _write_wav(path: Path, duration_seconds: float, generator: Callable[[int, int], float]) -> None:
    count = _sample_count(duration_seconds)
    samples = bytearray()
    for index in range(count):
        value = max(-1.0, min(1.0, generator(index, count)))
        samples.extend(round(value * PCM_MAX).to_bytes(2, byteorder="little", signed=True))
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(SAMPLE_RATE_HZ)
        file.writeframes(samples)


def create_sample_set(output_directory: str | Path) -> Path:
    """Create reproducible WAV fixtures and return their JSON manifest path."""
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    definitions: list[tuple[str, float, Callable[[int, int], float], dict[str, object]]] = [
        ("silence_2s.wav", 2, lambda _index, _total: 0, {"type": "silence"}),
        *[
            (
                f"tone_{frequency}hz.wav",
                3,
                _sine(frequency, 0.7),
                {"type": "tone", "frequency_hz": frequency, "amplitude": 0.7},
            )
            for frequency in (86, 172, 344, 688, 1376, 2752, 5504)
        ],
        (
            "multi_tone_220_1000_4000hz.wav",
            3,
            _multitone([220, 1000, 4000], 0.75),
            {"type": "multi_tone", "frequencies_hz": [220, 1000, 4000], "amplitude": 0.75},
        ),
        (
            "sweep_80_8000hz.wav",
            5,
            _sweep(80, 8000, 5, 0.7),
            {"type": "logarithmic_sweep", "start_hz": 80, "end_hz": 8000, "amplitude": 0.7},
        ),
        (
            "click_train_500ms.wav",
            4,
            _click_train(0.5, 0.9),
            {"type": "click_train", "interval_ms": 500, "click_duration_ms": 10, "amplitude": 0.9},
        ),
        (
            "amplitude_modulated_440hz_2hz.wav",
            5,
            _amplitude_modulated(440, 2, 0.7),
            {"type": "amplitude_modulation", "carrier_hz": 440, "modulation_hz": 2, "amplitude": 0.7},
        ),
        (
            "white_noise_seed_20261108.wav",
            4,
            _white_noise(20261108, 0.55),
            {"type": "white_noise", "seed": 20261108, "amplitude": 0.55},
        ),
        (
            "pink_noise_seed_20261108.wav",
            4,
            _pink_noise(20261108, 0.55),
            {"type": "pink_noise", "seed": 20261108, "amplitude": 0.55},
        ),
    ]
    files = []
    for filename, duration_seconds, generator, parameters in definitions:
        _write_wav(directory / filename, duration_seconds, generator)
        files.append(
            {
                "id": filename.removesuffix(".wav"),
                "filename": filename,
                "duration_seconds": duration_seconds,
                **parameters,
            }
        )
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sample_rate_hz": SAMPLE_RATE_HZ,
                "channels": 1,
                "encoding": "PCM signed 16-bit little-endian WAV",
                "files": files,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path
