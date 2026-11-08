"""Human-readable rendering for machine-readable comparison reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_summary(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    lines = [
        f"AudioReactive comparison: {report['case_id']}",
        f"Verdict: {report['verdict']} (exit {report['exit_code']})",
        (
            f"Coverage: {coverage['paired_packets']}/{coverage['reference_packets']} reference packets "
            f"({coverage['ratio']:.1%})"
        ),
    ]
    if report["validity_errors"]:
        lines.extend(["", "Invalid or inconclusive evidence:"])
        lines.extend(f"- {error}" for error in report["validity_errors"])
    if report["threshold_failures"]:
        lines.extend(["", "Threshold failures:"])
        for failure in report["threshold_failures"]:
            if "observed" in failure:
                lines.append(
                    f"- {failure['field']}.{failure['statistic']}: "
                    f"{failure['observed']} > {failure['maximum']}"
                )
            else:
                lines.append(f"- {failure['field']}: {failure['reason']}")
    if report["diagnostics"]:
        lines.extend(["", "Diagnostic hints:"])
        lines.extend(f"- [{hint['category']}] {hint['hint']} Evidence: {hint['evidence']}" for hint in report["diagnostics"])
    return "\n".join(lines) + "\n"


def write_summary(path: str | Path, report: dict[str, Any]) -> None:
    Path(path).write_text(render_summary(report), encoding="utf-8")
