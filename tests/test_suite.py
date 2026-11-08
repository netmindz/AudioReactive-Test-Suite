import base64
import json
import math
import socket
import struct
import tempfile
import threading
import sys
import wave
import unittest
from pathlib import Path

from audioreactive_suite.cli import main
from audioreactive_suite.compare import EXIT_INCONCLUSIVE, EXIT_PASS, EXIT_THRESHOLD_FAILURE, compare
from audioreactive_suite.capture import capture_udp
from audioreactive_suite.execution import run_case
from audioreactive_suite.packet import decode_packet
from audioreactive_suite.stimuli import SAMPLE_RATE_HZ, create_sample_set


def audio_sync_packet(
    timestamp_ns: int,
    source: str,
    *,
    volume_raw: float = 1.0,
    volume_smoothed: float = 2.0,
    fft: tuple[int, ...] = tuple(range(16)),
    magnitude: float = 3.0,
    dominant_frequency: float = 440.0,
    peak: bool = False,
) -> dict:
    payload = bytearray(44)
    payload[:6] = b"00002\x00"
    struct.pack_into("<ff", payload, 8, volume_raw, volume_smoothed)
    payload[16] = int(peak)
    payload[18:34] = bytes(fft)
    struct.pack_into("<ff", payload, 36, magnitude, dominant_frequency)
    return {
        "timestamp_ns": timestamp_ns,
        "source": source,
        "data_b64": base64.b64encode(payload).decode("ascii"),
    }


def write_capture(path: Path, identity: str, packets: list[dict], settings: dict | None = None) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metadata": {"identity": identity, "settings": settings or {"fft_size": 512}},
                "packets": packets,
            }
        ),
        encoding="utf-8",
    )


def write_case(path: Path, **overrides: object) -> None:
    case = {
        "id": "test-tone",
        "required_settings": ["fft_size"],
        "alignment": {"candidate_offset_ms": 0, "maximum_pair_distance_ms": 10},
        "minimum_coverage": 1,
        "thresholds": {"volume_raw": {"mae": 0.01}, "fft.0": {"mae": 0}},
    }
    case.update(overrides)
    path.write_text(json.dumps(case), encoding="utf-8")


class PacketTests(unittest.TestCase):
    def test_decodes_strict_v2_packet(self) -> None:
        decoded = decode_packet(audio_sync_packet(1, "127.0.0.1:11988", peak=True))
        self.assertEqual(decoded.fft, tuple(range(16)))
        self.assertTrue(decoded.peak)
        self.assertEqual(decoded.dominant_frequency, 440.0)

    def test_rejects_non_finite_float(self) -> None:
        packet = audio_sync_packet(1, "127.0.0.1:11988", volume_raw=math.nan)
        with self.assertRaisesRegex(ValueError, "non-finite"):
            decode_packet(packet)


class CaptureTests(unittest.TestCase):
    def test_captures_udp_datagram_with_source_and_monotonic_timestamp(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        def send_packet() -> None:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"test", ("127.0.0.1", port))

        timer = threading.Timer(0.05, send_packet)
        timer.start()
        try:
            packets = capture_udp("127.0.0.1", port, 0.3, {"identity": "capture"})
        finally:
            timer.join()
        self.assertEqual(len(packets), 1)
        self.assertGreater(packets[0]["timestamp_ns"], 0)
        self.assertIn(":", packets[0]["source"])


class StimulusTests(unittest.TestCase):
    def test_creates_complete_pcm_fixture_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = create_sample_set(directory)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["files"]), 14)
            self.assertEqual(manifest["sample_rate_hz"], SAMPLE_RATE_HZ)
            self.assertEqual(manifest["files"][-1]["seed"], 20261108)
            with wave.open(str(Path(directory) / "sweep_80_8000hz.wav")) as audio:
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getsampwidth(), 2)
                self.assertEqual(audio.getframerate(), SAMPLE_RATE_HZ)
                self.assertEqual(audio.getnframes(), 5 * SAMPLE_RATE_HZ)


class ComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.reference = self.root / "reference.json"
        self.candidate = self.root / "candidate.json"
        self.case = self.root / "case.json"
        self.report = self.root / "report.json"
        reference_packets = [
            audio_sync_packet(1_000_000_000, "10.0.0.1:11988", peak=True),
            audio_sync_packet(1_020_000_000, "10.0.0.1:11988"),
        ]
        candidate_packets = [
            audio_sync_packet(1_000_000_000, "10.0.0.2:11988", peak=True),
            audio_sync_packet(1_020_000_000, "10.0.0.2:11988"),
        ]
        write_capture(self.reference, "reference", reference_packets)
        write_capture(self.candidate, "candidate", candidate_packets)
        write_case(self.case)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_matching_artifacts_pass_and_cli_writes_report(self) -> None:
        exit_code = main(
            [
                "compare",
                "--reference",
                str(self.reference),
                "--candidate",
                str(self.candidate),
                "--case",
                str(self.case),
                "--output",
                str(self.report),
            ]
        )
        self.assertEqual(exit_code, EXIT_PASS)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(report["verdict"], "pass")
        self.assertFalse(report["quality"]["reference"]["frame_counter_observed"])

    def test_value_difference_fails_declared_threshold(self) -> None:
        write_capture(
            self.candidate,
            "candidate",
            [
                audio_sync_packet(1_000_000_000, "10.0.0.2:11988", volume_raw=4),
                audio_sync_packet(1_020_000_000, "10.0.0.2:11988", volume_raw=4),
            ],
        )
        report, exit_code = compare(self.reference, self.candidate, self.case)
        self.assertEqual(exit_code, EXIT_THRESHOLD_FAILURE)
        self.assertEqual(report["verdict"], "threshold_failure")
        self.assertEqual(report["threshold_failures"][0]["field"], "volume_raw")

    def test_mismatched_required_setting_is_inconclusive(self) -> None:
        write_capture(
            self.candidate,
            "candidate",
            [audio_sync_packet(1_000_000_000, "10.0.0.2:11988")],
            settings={"fft_size": 256},
        )
        report, exit_code = compare(self.reference, self.candidate, self.case)
        self.assertEqual(exit_code, EXIT_INCONCLUSIVE)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn("required setting 'fft_size' differs", report["validity_errors"])

    def test_strong_signal_frequency_is_excluded_below_configured_magnitude(self) -> None:
        write_case(
            self.case,
            strong_signal_magnitude=5,
            thresholds={"dominant_frequency": {"mae": 0}},
        )
        report, exit_code = compare(self.reference, self.candidate, self.case)
        self.assertEqual(exit_code, EXIT_THRESHOLD_FAILURE)
        self.assertEqual(report["eligibility"]["dominant_frequency_pairs"], 0)
        self.assertEqual(report["threshold_failures"][0]["field"], "dominant_frequency")

    def test_reports_configured_window_metrics(self) -> None:
        write_case(
            self.case,
            analysis_windows_ms=[{"id": "early", "start": 0, "end": 25}],
        )
        report, exit_code = compare(self.reference, self.candidate, self.case)
        self.assertEqual(exit_code, EXIT_PASS)
        self.assertEqual(report["window_metrics"]["early"]["volume_raw"]["samples"], 2)


class ExecutionTests(unittest.TestCase):
    def test_run_captures_declared_endpoints_and_writes_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            audio.write_bytes(b"fixture")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("127.0.0.1", 0))
                listener_port = probe.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("127.0.0.1", 0))
                reference_port = probe.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("127.0.0.1", 0))
                candidate_port = probe.getsockname()[1]

            packet = audio_sync_packet(1, "unused")["data_b64"]
            command = [
                sys.executable,
                "-c",
                (
                    "import base64,socket,time,sys; p=base64.b64decode(sys.argv[1]); "
                    "a=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); b=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); "
                    "a.bind(('127.0.0.1',int(sys.argv[3]))); b.bind(('127.0.0.1',int(sys.argv[4]))); "
                    "[(a.sendto(p,('127.0.0.1',int(sys.argv[2]))),b.sendto(p,('127.0.0.1',int(sys.argv[2]))),time.sleep(.02)) "
                    "for _ in range(3)]; a.close(); b.close()"
                ),
                packet,
                str(listener_port),
                str(reference_port),
                str(candidate_port),
            ]
            case = root / "case.json"
            write_case(
                case,
                alignment={"candidate_offset_ms": 0, "maximum_pair_distance_ms": 30},
                timing={"capture_duration_seconds": 0.2},
            )
            report, exit_code = run_case(
                case,
                audio,
                command,
                root / "run",
                "127.0.0.1",
                listener_port,
                f"127.0.0.1:{reference_port}",
                f"127.0.0.1:{candidate_port}",
                {"identity": "reference", "settings": {"fft_size": 512}},
                {"identity": "candidate", "settings": {"fft_size": 512}},
            )
            self.assertEqual(exit_code, EXIT_PASS, report)
            self.assertEqual(report["verdict"], "pass")
            self.assertTrue((root / "run" / "report.json").is_file())
            self.assertTrue((root / "run" / "summary.txt").is_file())
