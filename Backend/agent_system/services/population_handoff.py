"""Durable completion contract for the study population pipeline stage."""

from __future__ import annotations

import json
import csv
from pathlib import Path


POPULATION_AGENT_ID = "study_population_planner_agent"


def population_handoff_issues(workspace_folder: str | Path) -> list[str]:
    """Return every missing/invalid artifact that makes handoff unsafe.

    A model response is not proof that the population stage completed. The
    downstream pipeline may start only after anatomy validation, the HITL
    targets gate, and the population agent's persisted completion records all
    exist in the workspace.
    """
    workspace = Path(workspace_folder)
    issues: list[str] = []

    config_path = workspace / "config.json"
    config: dict | None = None
    if not config_path.is_file():
        issues.append("config.json is missing")
    else:
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config = loaded
            else:
                issues.append("config.json must contain a JSON object")
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"config.json is unreadable: {exc}")

    if config is not None:
        if config.get("anatomy_validated") is not True:
            issues.append("validate_population_anatomy has not completed")
        if "datasetlist" not in config:
            issues.append("config.json has no validated datasetlist")
        elif not isinstance(config["datasetlist"], list):
            issues.append("config.json datasetlist must be a list")
        elif not config["datasetlist"]:
            issues.append("validated datasetlist is empty; at least one cohort volume is required")
        if config.get("cohort_approved") is not True:
            issues.append("doctor cohort approval has not completed")

    targets_path = workspace / "populations" / "targets" / "targets.csv"
    if not targets_path.is_file():
        issues.append("generate_targets has not produced populations/targets/targets.csv")
    else:
        try:
            with targets_path.open(mode="r", encoding="utf-8", newline="") as target_file:
                if not any(csv.DictReader(target_file)):
                    issues.append("targets.csv has no cohort rows")
        except (OSError, csv.Error) as exc:
            issues.append(f"targets.csv is unreadable: {exc}")

    scratchpad_path = workspace / "scratchpad.json"
    if not scratchpad_path.is_file():
        issues.append("scratchpad.json is missing")
    else:
        try:
            scratchpad = json.loads(scratchpad_path.read_text(encoding="utf-8"))
            agent_state = scratchpad.get("agents", {}).get(POPULATION_AGENT_ID, {})
            if agent_state.get("status") != "done":
                issues.append("population scratchpad status is not done")
            summary = agent_state.get("summary")
            if not isinstance(summary, dict) or not str(summary.get("content", "")).strip():
                issues.append("population scratchpad summary is missing")
        except (AttributeError, OSError, json.JSONDecodeError) as exc:
            issues.append(f"scratchpad.json is unreadable: {exc}")

    output_path = workspace / "outputs" / f"{POPULATION_AGENT_ID}_output.json"
    if not output_path.is_file():
        issues.append("population structured output is missing")
    else:
        try:
            output = json.loads(output_path.read_text(encoding="utf-8"))
            if not isinstance(output, dict):
                issues.append("population structured output must contain a JSON object")
            elif output.get("agent_id") != POPULATION_AGENT_ID:
                issues.append("population structured output has the wrong agent_id")
            elif output.get("status") != "completed":
                issues.append("population structured output status is not completed")
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"population structured output is unreadable: {exc}")

    return issues


def require_population_handoff(workspace_folder: str | Path) -> None:
    """Raise when the population agent has not completed its durable contract."""
    issues = population_handoff_issues(workspace_folder)
    if issues:
        raise RuntimeError("Population handoff is incomplete: " + "; ".join(issues))
