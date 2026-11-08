"""Command-line interface for capture and offline comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .capture import capture_udp, write_capture
from .compare import compare, write_report
from .execution import run_case
from .reporting import render_summary, write_summary
from .stimuli import create_sample_set


def _metadata(value: str) -> dict[str, Any]:
    try:
        metadata = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("metadata must be valid JSON") from error
    if not isinstance(metadata, dict):
        raise argparse.ArgumentTypeError("metadata must be a JSON object")
    return metadata


def _command(value: str) -> list[str]:
    try:
        command = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("playback command must be valid JSON") from error
    if not isinstance(command, list):
        raise argparse.ArgumentTypeError("playback command must be a JSON string array")
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="audioreactive-suite")
    commands = parser.add_subparsers(dest="command", required=True)

    capture = commands.add_parser("capture", help="capture UDP datagrams into a JSON artifact")
    capture.add_argument("--host", default="0.0.0.0")
    capture.add_argument("--port", type=int, required=True)
    capture.add_argument("--duration", type=float, required=True)
    capture.add_argument("--metadata", type=_metadata, required=True)
    capture.add_argument("--multicast-group")
    capture.add_argument("--output", type=Path, required=True)

    comparison = commands.add_parser("compare", help="compare two saved capture artifacts")
    comparison.add_argument("--reference", type=Path, required=True)
    comparison.add_argument("--candidate", type=Path, required=True)
    comparison.add_argument("--case", type=Path, required=True)
    comparison.add_argument("--output", type=Path, required=True)
    comparison.add_argument("--summary", type=Path)

    samples = commands.add_parser("generate-samples", help="create deterministic WAV test fixtures")
    samples.add_argument("--output-dir", type=Path, required=True)

    run = commands.add_parser("run", help="play a fixture, capture declared sources, and compare them")
    run.add_argument("--case", type=Path, required=True)
    run.add_argument("--audio", type=Path, required=True)
    run.add_argument("--playback-command", type=_command, required=True)
    run.add_argument("--work-dir", type=Path, required=True)
    run.add_argument("--host", default="0.0.0.0")
    run.add_argument("--port", type=int, required=True)
    run.add_argument("--reference-endpoint", required=True)
    run.add_argument("--candidate-endpoint", required=True)
    run.add_argument("--reference-metadata", type=_metadata, required=True)
    run.add_argument("--candidate-metadata", type=_metadata, required=True)
    run.add_argument("--multicast-group")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "capture":
        packets = capture_udp(args.host, args.port, args.duration, args.metadata, args.multicast_group)
        write_capture(args.output, args.metadata, packets)
        print(f"captured {len(packets)} datagrams to {args.output}")
        return 0
    if args.command == "generate-samples":
        manifest_path = create_sample_set(args.output_dir)
        print(f"generated sample audio manifest: {manifest_path}")
        return 0
    if args.command == "run":
        report, exit_code = run_case(
            args.case,
            args.audio,
            args.playback_command,
            args.work_dir,
            args.host,
            args.port,
            args.reference_endpoint,
            args.candidate_endpoint,
            args.reference_metadata,
            args.candidate_metadata,
            args.multicast_group,
        )
        print(render_summary(report), end="")
        return exit_code

    report, exit_code = compare(args.reference, args.candidate, args.case)
    write_report(args.output, report)
    if args.summary:
        write_summary(args.summary, report)
    print(render_summary(report), end="")
    return exit_code
