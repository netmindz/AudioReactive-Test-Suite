"""Strict decoder for the 44-byte Audio Sync v2 datagram."""

from __future__ import annotations

import base64
import math
import struct
from dataclasses import dataclass
from typing import Any


V2_HEADER = b"00002\x00"
V2_LENGTH = 44


@dataclass(frozen=True)
class DecodedPacket:
    timestamp_ns: int
    source: str
    volume_raw: float
    volume_smoothed: float
    peak: bool
    fft: tuple[int, ...]
    magnitude: float
    dominant_frequency: float
    frame_counter: int
    extension: bytes


def decode_packet(record: dict[str, Any]) -> DecodedPacket:
    payload = base64.b64decode(record["data_b64"], validate=True)
    if len(payload) != V2_LENGTH:
        raise ValueError(f"expected {V2_LENGTH} bytes, received {len(payload)}")
    if payload[:6] != V2_HEADER:
        raise ValueError("unsupported Audio Sync header")

    volume_raw, volume_smoothed = struct.unpack_from("<ff", payload, 8)
    magnitude = struct.unpack_from("<f", payload, 36)[0]
    dominant_frequency = struct.unpack_from("<f", payload, 40)[0]
    numeric_values = (volume_raw, volume_smoothed, magnitude, dominant_frequency)
    if not all(math.isfinite(value) for value in numeric_values):
        raise ValueError("packet contains a non-finite numeric field")

    return DecodedPacket(
        timestamp_ns=record["timestamp_ns"],
        source=record["source"],
        volume_raw=volume_raw,
        volume_smoothed=volume_smoothed,
        peak=payload[16] != 0,
        frame_counter=payload[17],
        fft=tuple(payload[18:34]),
        magnitude=magnitude,
        dominant_frequency=dominant_frequency,
        extension=payload[6:8] + payload[34:36],
    )
