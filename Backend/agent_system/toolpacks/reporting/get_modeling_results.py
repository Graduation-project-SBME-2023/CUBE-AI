"""Read recorded modeling results for reporting."""

from __future__ import annotations

from typing import Any, Iterable

from . import mcp
from .helpers import domain_api
from tool_logging import ToolEvent, log_tool_state, TextPayload

_HEADLINE_METRICS = ("accuracy", "auc", "f1", "precision", "recall", "specificity", "sensitivity")


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value).replace("|", "/")


def _table(header: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    header = list(header)
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return "\n".join(lines)


def _feature_section(feature_sets: list[dict[str, Any]]) -> str:
    if not feature_sets:
        return "### Features\n*No frozen feature set is recorded for this session.*"
    rows = []
    for item in feature_sets:
        selection = item.get("selection") or {}
        rows.append(
            [
                item.get("set_key"),
                item.get("feature_set_id"),
                item.get("feature_count"),
                item.get("subject_count"),
                "yes" if selection.get("label_supervised_on_full_data") else "no",
                ", ".join((item.get("feature_names") or [])[:10]),
            ]
        )
    return "### Features\n" + _table(
        ["Set", "Feature set id", "Features", "Subjects", "Selected on every label", "First features"],
        rows,
    )


def _comparison_section(entry: dict[str, Any]) -> str:
    selected = entry.get("selected") or {}
    metrics = selected.get("metrics") or {}
    evaluation = metrics.get("evaluation") or {}
    headline = evaluation.get("metrics") or {}
    feature_set = entry.get("feature_set") or {}
    source = (
        f"feature set `{feature_set.get('set_key')}` ({feature_set.get('feature_count')} features)"
        if feature_set
        else f"captured input CSV (artifact {entry.get('input_file_id')})"
    )
    parts = [
        f"### Comparison `{entry.get('comparison_key')}`",
        f"- Trained on: {source}",
        f"- Evaluation: {evaluation.get('kind', 'unknown')} on {evaluation.get('n_cases', '?')} case(s)",
        f"- Selected model: {selected.get('algorithm', '—')} (`{selected.get('variant_key', '—')}`)",
    ]
    if not evaluation.get("generalisation_estimate"):
        parts.append(
            "- **Caveat:** these metrics are NOT a generalisation estimate — the features "
            "were selected with the evaluated rows' labels, or no withheld rows were used. "
            "Report them as optimistic."
        )
    if headline:
        parts.append(
            _table(
                ["Metric", "Value"],
                [[name, headline.get(name)] for name in _HEADLINE_METRICS if name in headline],
            )
        )
    variants = entry.get("variants") or []
    if variants:
        parts.append(
            _table(
                ["Variant", "Algorithm", "Status", "Selected", "CV AUC", "CV Accuracy", "Eval AUC"],
                [
                    [
                        variant.get("variant_key"),
                        variant.get("algorithm"),
                        variant.get("status"),
                        "yes" if variant.get("selected") else "",
                        ((variant.get("metrics") or {}).get("cv") or {}).get("AUC"),
                        ((variant.get("metrics") or {}).get("cv") or {}).get("Accuracy"),
                        (((variant.get("metrics") or {}).get("evaluation") or {}).get("metrics") or {}).get("auc"),
                    ]
                    for variant in variants
                ],
            )
        )
    return "\n".join(parts)


@mcp.tool()
async def get_modeling_results() -> str:
    """Retrieves the recorded feature sets and modeling results of this session in one request — everything needed to write the results report."""
    log_tool_state(ToolEvent("tool_started", "text", TextPayload(message="fetching results")))
    try:
        feature_sets = await domain_api.list_feature_sets()
        summary = await domain_api.model_results_summary()
    except domain_api.DomainApiError as exc:
        return f"Error: the modeling results could not be read from the Application API: {exc}"

    sections = [_feature_section(feature_sets)]
    if summary:
        sections += [_comparison_section(entry) for entry in summary]
    else:
        sections.append("### Modeling\n*No model result is recorded for this session.*")
    log_tool_state(
        ToolEvent("tool_finished", "text", TextPayload(message="study data results Fetched"))
    )
    return "\n\n".join(sections)
