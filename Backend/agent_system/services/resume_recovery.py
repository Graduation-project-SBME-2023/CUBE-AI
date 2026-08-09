import json
import logging
import shutil
from pathlib import Path
from storage_paths import workspace_subpath

logger = logging.getLogger(__name__)


# Agent-owned runtime artifacts. Completed predecessors are deliberately absent
# from a reset unless they are themselves the interrupted agent.
AGENT_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "study_population_planner_agent": (
        "populations",
        "clinical_data.json",
        "clinical_data.csv",
        "outputs/study_population_planner_agent_output.json",
    ),
    "segmentation_agent": (
        "volumes",
        "masks",
        "overlays",
        "temp_segmentations",
        "review_result.json",
        "segmentation_manifest.json",
        "outputs/segmentation_agent_output.json",
    ),
    "feature_extraction_agent": (
        "features",
        "outputs/feature_extraction_agent_output.json",
    ),
    "modeling_agent": (
        "modeling",
        "models",
        "outputs/modeling_agent_output.json",
    ),
    "result_reporter_agent": (
        "outputs/result_reporter_agent_output.json",
    ),
    "latex_reporter_agent": (
        "reports",
        "outputs/latex_reporter_agent_output.json",
    ),
}


def _validated_workspace(workspace_root: str | Path, session_id: str) -> Path:
    root = Path(workspace_root).resolve()
    workspace = (root / f"workspace_{session_id}").resolve()
    if workspace.parent != root:
        raise ValueError("Resolved session workspace is outside AGENTS_WORKSPACE_DIR")
    return workspace


def _remove_path(path: Path) -> bool:
    if path.is_dir():
        shutil.rmtree(path)
        return True
    if path.exists():
        path.unlink()
        return True
    return False


def _reset_scratchpad(workspace: Path, agent_id: str) -> bool:
    scratchpad_path = workspace_subpath(workspace, "scratchpad.json")
    if not scratchpad_path.exists():
        return False

    try:
        scratchpad = json.loads(scratchpad_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cannot safely reset malformed scratchpad: {scratchpad_path}"
        ) from exc

    changed = False
    agents = scratchpad.get("agents")
    if isinstance(agents, dict) and agent_id in agents:
        del agents[agent_id]
        changed = True

    activity_log = scratchpad.get("activity_log")
    if isinstance(activity_log, list):
        filtered = [
            entry
            for entry in activity_log
            if not (
                isinstance(entry, dict)
                and str(entry.get("agent_id") or entry.get("agent") or "") == agent_id
            )
        ]
        if len(filtered) != len(activity_log):
            scratchpad["activity_log"] = filtered
            changed = True

    if changed:
        scratchpad_path.write_text(
            json.dumps(scratchpad, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return changed


def _reset_population_config(workspace: Path) -> bool:
    """Keep the user-selected labelling mode but remove population run state."""
    config_path = workspace_subpath(workspace, "config.json")
    if not config_path.exists():
        return False
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cannot safely reset malformed population config: {config_path}"
        ) from exc

    preserved = {}
    if isinstance(config, dict) and "labelling_mode" in config:
        preserved["labelling_mode"] = config["labelling_mode"]
    config_path.write_text(
        json.dumps(preserved, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return True


def _has_resumable_segmentation_checkpoint(workspace: Path) -> bool:
    """Return true when pair processing or review can resume safely."""
    manifest_path = workspace_subpath(workspace, "segmentation_manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("stage") not in {"processing", "awaiting_review", "reviewed"}
            or not isinstance(manifest.get("pairs"), list)
        ):
            return False
        for item in manifest["pairs"]:
            if not isinstance(item, dict) or item.get("status") != "ready":
                continue
            volume_path = workspace_subpath(workspace, f"volumes/{item['volume_uid']}.nii.gz")
            mask_path = workspace_subpath(workspace, str(item.get("mask_path") or ""))
            mask_path.relative_to(workspace)
            if not volume_path.is_file() or not mask_path.is_file():
                return False
        return bool(manifest["pairs"])
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return False


def reset_interrupted_agent(
    workspace_root: str | Path,
    session_id: str,
    agent_id: str,
) -> list[str]:
    """
    Delete only the interrupted agent's restart-unsafe local artifacts.

    The operation is idempotent so a failed persistence call can be retried
    safely. Planner state and completed predecessor outputs remain untouched.
    """
    if agent_id not in AGENT_ARTIFACTS:
        raise ValueError(f"No recovery artifact policy exists for agent {agent_id!r}")

    workspace = _validated_workspace(workspace_root, session_id)
    workspace.mkdir(parents=True, exist_ok=True)

    artifacts = AGENT_ARTIFACTS[agent_id]
    if (
        agent_id == "segmentation_agent"
        and _has_resumable_segmentation_checkpoint(workspace)
    ):
        artifacts = ("outputs/segmentation_agent_output.json",)
        logger.info(
            "[%s] Preserving resumable segmentation review checkpoint",
            session_id,
        )

    removed: list[str] = []
    for relative in artifacts:
        target = workspace_subpath(workspace, relative)
        try:
            target.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"Unsafe artifact path for {agent_id}: {relative}") from exc
        if _remove_path(target):
            removed.append(relative)

    if _reset_scratchpad(workspace, agent_id):
        removed.append("scratchpad.json (agent entry)")
    if agent_id == "study_population_planner_agent" and _reset_population_config(
        workspace
    ):
        removed.append("config.json (population state)")

    logger.info(
        "[%s] Reset interrupted agent %s; cleared artifacts: %s",
        session_id,
        agent_id,
        removed or "none",
    )
    return removed
