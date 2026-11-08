"""Capture artifact persistence and bounded UDP capture."""

from __future__ import annotations

import base64
import binascii
import json
import socket
import time
from pathlib import Path
from typing import Any


CAPTURE_SCHEMA_VERSION = 1


def load_capture(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as file:
        artifact = json.load(file)
    if not isinstance(artifact, dict) or artifact.get("schema_version") != CAPTURE_SCHEMA_VERSION:
        raise ValueError(f"{path}: unsupported capture schema")
    if not isinstance(artifact.get("metadata"), dict) or not isinstance(artifact.get("packets"), list):
        raise ValueError(f"{path}: capture requires metadata and packets")
    for index, packet in enumerate(artifact["packets"]):
        if not isinstance(packet, dict):
            raise ValueError(f"{path}: packet {index} is not an object")
        if not isinstance(packet.get("timestamp_ns"), int) or packet["timestamp_ns"] < 0:
            raise ValueError(f"{path}: packet {index} has an invalid timestamp")
        if not isinstance(packet.get("source"), str) or not packet["source"]:
            raise ValueError(f"{path}: packet {index} has no source endpoint")
        try:
            base64.b64decode(packet["data_b64"], validate=True)
        except (KeyError, TypeError, ValueError, binascii.Error) as error:
            raise ValueError(f"{path}: packet {index} has invalid encoded data") from error
    return artifact


def write_capture(path: str | Path, metadata: dict[str, Any], packets: list[dict[str, Any]]) -> None:
    artifact = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "metadata": metadata,
        "packets": packets,
    }
    Path(path).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def capture_udp(
    host: str,
    port: int,
    duration_seconds: float,
    metadata: dict[str, Any],
    multicast_group: str | None = None,
) -> list[dict[str, Any]]:
    """Capture UDP datagrams for a bounded duration using a monotonic clock."""
    if duration_seconds <= 0:
        raise ValueError("duration must be greater than zero")

    packets: list[dict[str, Any]] = []
    deadline = time.monotonic() + duration_seconds
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        if multicast_group:
            membership = socket.inet_aton(multicast_group) + socket.inet_aton(host)
            listener.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            listener.settimeout(remaining)
            try:
                payload, endpoint = listener.recvfrom(65535)
            except TimeoutError:
                break
            packets.append(
                {
                    "timestamp_ns": time.monotonic_ns(),
                    "source": f"{endpoint[0]}:{endpoint[1]}",
                    "data_b64": base64.b64encode(payload).decode("ascii"),
                }
            )
    metadata.setdefault("capture_host", host)
    metadata.setdefault("capture_port", port)
    metadata.setdefault("capture_duration_seconds", duration_seconds)
    metadata.setdefault("captured_at_unix_ns", time.time_ns())
    return packets
