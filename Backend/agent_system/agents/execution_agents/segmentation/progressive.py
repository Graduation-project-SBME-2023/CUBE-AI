"""Pair identity, progressive preview publication, and replay helpers."""

import hashlib
import logging
from pathlib import Path
from typing import Any

from storage_paths import workspace_subpath

from socket_store import manager as ws_manager

logger = logging.getLogger(__name__)


def artifact_key_for_pair(pair_id: str) -> str:
    """Return a deterministic filesystem-safe key without changing pair identity."""
    return hashlib.sha256(pair_id.encode("utf-8")).hexdigest()


def ensure_artifact_key(item: dict[str, Any]) -> str:
    key = str(item.get("artifact_key") or "")
    if not key:
        key = artifact_key_for_pair(str(item["pair_id"]))
        item["artifact_key"] = key
    return key


def artifact_candidates(
    workspace_dir: str | Path,
    subdirectory: str,
    item: dict[str, Any],
    suffix: str,
) -> list[Path]:
    """Return hashed and legacy artifact paths, in preference order."""
    workspace = Path(workspace_dir).resolve()
    directory = workspace_subpath(workspace, subdirectory)
    if directory.parent != workspace:
        raise ValueError(f"Unsafe segmentation artifact directory: {subdirectory}")
    artifact_key = ensure_artifact_key(item)
    pair_id = str(item["pair_id"])
    hashed = (directory / f"{artifact_key}{suffix}").resolve()
    candidates = [hashed]
    legacy = (directory / f"{pair_id}{suffix}").resolve()
    if legacy.parent == directory:
        candidates.append(legacy)
    return candidates


def existing_artifact_path(
    workspace_dir: str | Path,
    subdirectory: str,
    item: dict[str, Any],
    suffix: str,
) -> Path | None:
    for candidate in artifact_candidates(workspace_dir, subdirectory, item, suffix):
        if candidate.is_file():
            return candidate
    return None


def preview_from_item(
    item: dict[str, Any],
    workspace_dir: str | Path,
    base_url: str,
) -> dict[str, Any] | None:
    # A rejected pair still belongs in a historical QC snapshot. Its source
    # mask may have been discarded, but the generated overlay and the user's
    # decision must remain visible when the session is reopened.
    if item.get("status") not in {"ready", "rejected"}:
        return None
    gif_path = str(item.get("gif_path") or "")
    if not gif_path:
        existing = existing_artifact_path(workspace_dir, "overlays", item, ".gif")
        if existing is None:
            return None
        gif_path = f"overlays/{existing.name}"
        item["gif_path"] = gif_path
    workspace = Path(workspace_dir).resolve()
    overlays = workspace_subpath(workspace, "overlays")
    absolute = workspace_subpath(workspace, gif_path)
    try:
        absolute.relative_to(overlays)
    except ValueError:
        logger.warning("Ignoring unsafe GIF path for pair %s: %s", item["pair_id"], gif_path)
        return None
    if not absolute.is_file() or absolute.stat().st_size <= 0:
        return None
    workspace_name = Path(workspace_dir).name
    return {
        "seriesInstanceUid": item["volume_uid"],
        "pairId": item["pair_id"],
        "targetId": item.get("target_id"),
        "targetLabel": item.get("canonical_label"),
        "gifUrl": (
            f"{base_url.rstrip('/')}/files/{workspace_name}/"
            f"{Path(gif_path).as_posix()}"
        ),
        "isEmptyMask": bool(item.get("is_empty_mask")),
        "warning": item.get("warning"),
        "reviewStatus": item.get("review_status", "pending"),
    }


def build_segmentation_snapshot(
    *,
    plan,
    work_items: list[dict[str, Any]],
    workspace_dir: str | Path,
    base_url: str,
    is_finished: bool,
) -> dict[str, Any]:
    ordered = sorted(work_items, key=lambda item: int(item.get("sequence_index", 0)))
    previews = [
        preview
        for item in ordered
        if (preview := preview_from_item(item, workspace_dir, base_url)) is not None
    ]
    failures = [
        {
            "seriesInstanceUid": item["volume_uid"],
            "pairId": item["pair_id"],
            "targetId": item.get("target_id"),
            "reason": item.get("error") or "Segmentation pair failed",
            "stage": item.get("error_stage"),
            "reasonCode": (
                (item.get("error_details") or {}).get("reason_code")
                if isinstance(item.get("error_details"), dict)
                else None
            ),
        }
        for item in ordered
        if item.get("status") == "failed"
    ]
    preview_keys = {entry["pairId"] for entry in previews}
    failure_keys = {entry["pairId"] for entry in failures}
    overlap = preview_keys & failure_keys
    if overlap:
        raise ValueError(f"Pairs cannot be both ready and failed: {sorted(overlap)}")
    if is_finished and len(previews) + len(failures) != len(work_items):
        raise ValueError(
            "Final segmentation snapshot requires every pair to have exactly one "
            "ready preview or failure"
        )

    if hasattr(plan, "target_labels"):
        target_labels = list(plan.target_labels)
        selected_models = list(plan.selected_models)
        targets = [
            {
                "targetId": target.target_id,
                "canonicalLabel": target.canonical_label,
            }
            for target in plan.targets
        ]
    else:
        target_records = list(plan.get("targets") or [])
        target_labels = [
            str(target.get("canonical_label") or target.get("canonicalLabel") or "")
            for target in target_records
        ]
        selected_models = list(plan.get("selected_models") or [])
        targets = [
            {
                "targetId": target.get("target_id") or target.get("targetId"),
                "canonicalLabel": target.get("canonical_label") or target.get("canonicalLabel"),
            }
            for target in target_records
        ]

    return {
        "runId": next(
            (str(item["run_id"]) for item in ordered if item.get("run_id")),
            None,
        ),
        "targetRoi": ", ".join(label for label in target_labels if label),
        "targets": targets,
        "selectedModel": ", ".join(str(model) for model in selected_models),
        "volumes": previews,
        "failedVolumes": failures,
        "progress": {
            "total": len(work_items),
            "generated": len(previews),
            "failed": len(failures),
        },
        "isFinished": is_finished,
    }


def build_snapshot_from_manifest(
    manifest: dict[str, Any],
    workspace_dir: str | Path,
    base_url: str,
) -> dict[str, Any] | None:
    work_items = manifest.get("pairs")
    final_plan = manifest.get("resolution", {}).get("final_plan")
    if not isinstance(work_items, list) or not isinstance(final_plan, dict):
        return None
    return build_segmentation_snapshot(
        plan=final_plan,
        work_items=work_items,
        workspace_dir=workspace_dir,
        base_url=base_url,
        is_finished=manifest.get("stage") in {"awaiting_review", "reviewed"},
    )


class ProgressivePreviewPublisher:
    """The single owner of cumulative segmentation preview snapshots."""

    def __init__(
        self,
        *,
        plan,
        work_items: list[dict[str, Any]],
        workspace_dir: str,
        session_id: str | None,
        base_url: str,
    ):
        self.plan = plan
        self.work_items = work_items
        self.workspace_dir = workspace_dir
        self.session_id = session_id
        self.base_url = base_url.rstrip("/")

    def build_snapshot(self, *, is_finished: bool) -> dict[str, Any]:
        return build_segmentation_snapshot(
            plan=self.plan,
            work_items=self.work_items,
            workspace_dir=self.workspace_dir,
            base_url=self.base_url,
            is_finished=is_finished,
        )

    async def publish(self, *, is_finished: bool = False) -> bool:
        """Hand the snapshot straight to the manager (WS-06/W10): this runs
        inside the same process as the socket routing table, so there is no
        HTTP hop to retry, and a UI-notification failure must never raise
        into segmentation logic -- log and move on instead."""
        payload = self.build_snapshot(is_finished=is_finished)
        try:
            await ws_manager.send_ws_message(
                "segmentation_output", payload, session_id=self.session_id
            )
            return True
        except Exception as exc:
            logger.warning(
                "Could not publish %s segmentation snapshot: %s",
                "final" if is_finished else "progressive",
                exc,
                exc_info=True,
            )
            return False

    async def publish_ready(self) -> bool:
        return await self.publish(is_finished=False)

    async def publish_failure(self) -> bool:
        return await self.publish(is_finished=False)

    async def publish_final(self) -> bool:
        return await self.publish(is_finished=True)
