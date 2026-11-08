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
    scorecard = report.get("scorecard", {})
    if scorecard:
        shape = scorecard.get("mean_shape_correlation")
        level = scorecard.get("mean_absolute_level_error_db")
        shape_text = f"{shape:.3f}" if isinstance(shape, (int, float)) else "n/a"
        level_text = f"{level:.2f} dB" if isinstance(level, (int, float)) else "n/a"
        lines.append(f"Band scorecard: mean temporal shape correlation {shape_text}; mean absolute level error {level_text}")
    model = report.get("reference_model")
    if isinstance(model, dict) and model.get("best_match"):
        lines.append(f"Closest simplified DSP fingerprint: {model['best_match']}")
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
