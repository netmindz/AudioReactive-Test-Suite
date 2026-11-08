"""Capture two declared senders while an external command plays a WAV stimulus."""

from __future__ import annotations

import base64
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from .capture import write_capture
from .compare import compare, load_case, write_report
from .reporting import write_summary


def _capture_duration(case: dict[str, Any]) -> float:
    timing = case.get("timing", {})
    if not isinstance(timing, dict):
        raise ValueError("timing must be an object")
    duration = timing.get("capture_duration_seconds")
    if not isinstance(duration, (int, float)) or duration <= 0:
        raise ValueError("timing.capture_duration_seconds must be greater than zero")
    return float(duration)


def _expand_command(command: Sequence[str], audio_path: Path) -> list[str]:
    if not command or not all(isinstance(part, str) and part for part in command):
        raise ValueError("playback command must be a non-empty JSON string array")
    audio = str(audio_path.resolve())
    return [part.replace("{audio}", audio) for part in command]


def run_case(
    case_path: str | Path,
    audio_path: str | Path,
    playback_command: Sequence[str],
    work_directory: str | Path,
    host: str,
    port: int,
    reference_endpoint: str,
    candidate_endpoint: str,
    reference_metadata: dict[str, Any],
    candidate_metadata: dict[str, Any],
    multicast_group: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Run playback and split one capture listener's traffic by declared source endpoint."""
    case_path = Path(case_path)
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise ValueError(f"audio fixture does not exist: {audio_path}")
    if reference_endpoint == candidate_endpoint:
        raise ValueError("reference and candidate endpoints must differ")
    case = load_case(case_path)
    duration = _capture_duration(case)
    work_directory = Path(work_directory)
    work_directory.mkdir(parents=True, exist_ok=True)
    command = _expand_command(playback_command, audio_path)
    records: list[dict[str, Any]] = []
    playback_started_ns: int | None = None
    playback_error: str | None = None

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        if multicast_group:
            listener.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_ADD_MEMBERSHIP,
                socket.inet_aton(multicast_group) + socket.inet_aton(host),
            )
        started = time.monotonic()
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        playback_started_ns = time.monotonic_ns()
        while (remaining := started + duration - time.monotonic()) > 0:
            listener.settimeout(remaining)
            try:
                payload, endpoint = listener.recvfrom(65535)
            except TimeoutError:
                break
            records.append(
                {
                    "timestamp_ns": time.monotonic_ns(),
                    "source": f"{endpoint[0]}:{endpoint[1]}",
                    "data_b64": base64.b64encode(payload).decode("ascii"),
                }
            )
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            playback_error = "playback command exceeded capture duration and termination grace period"
        if process.returncode != 0:
            playback_error = f"playback command exited {process.returncode}: {stderr.strip() or stdout.strip()}"

    reference_records = [record for record in records if record["source"] == reference_endpoint]
    candidate_records = [record for record in records if record["source"] == candidate_endpoint]
    ignored_sources = sorted({record["source"] for record in records} - {reference_endpoint, candidate_endpoint})
    common_metadata = {
        "case_id": case["id"],
        "audio_path": str(audio_path),
        "playback_command": command,
        "playback_started_monotonic_ns": playback_started_ns,
        "capture_host": host,
        "capture_port": port,
        "capture_duration_seconds": duration,
        "ignored_sources": ignored_sources,
    }
    reference_path = work_directory / "reference.capture.json"
    candidate_path = work_directory / "candidate.capture.json"
    write_capture(reference_path, {**reference_metadata, **common_metadata}, reference_records)
    write_capture(candidate_path, {**candidate_metadata, **common_metadata}, candidate_records)
    report, exit_code = compare(reference_path, candidate_path, case_path)
    report["execution"] = {
        "audio_path": str(audio_path),
        "playback_command": command,
        "playback_error": playback_error,
        "ignored_sources": ignored_sources,
    }
    if playback_error:
        report["validity_errors"].append(playback_error)
        report["verdict"] = "inconclusive"
        report["exit_code"] = 3
        exit_code = 3
    write_report(work_directory / "report.json", report)
    write_summary(work_directory / "summary.txt", report)
    return report, exit_code
