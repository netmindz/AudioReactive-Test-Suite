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
        level = scorecard.get("mean_absolute_output_value_ratio_error")
        shape_text = f"{shape:.3f}" if isinstance(shape, (int, float)) else "n/a"
        level_text = f"{level:.3f} ratio error" if isinstance(level, (int, float)) else "n/a"
        lines.append(
            f"Band scorecard: mean temporal shape correlation {shape_text}; "
            f"mean output-value ratio error {level_text} (level check: {scorecard.get('level_check', 'not calibrated')})"
        )
    model = report.get("reference_model")
    if isinstance(model, dict):
        baseline = model.get("reference_capture_ranking", {})
        candidate = model.get("candidate_capture_ranking", {})
        if baseline.get("ranking_is_trustworthy") and candidate.get("best_match"):
            lines.append(f"Closest simplified DSP fingerprint: {candidate['best_match']}")
        elif baseline:
            lines.append("DSP fingerprint ranking is untrusted: the reference capture did not validate the model.")
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
