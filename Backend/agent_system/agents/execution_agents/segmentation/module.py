"""Segmentation orchestration with agent-resolved clinical target identity."""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union
from uuid import uuid4

import httpx
import SimpleITK as sitk
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools

from agent_system.models.tool_logging import TextPayload
from client_config import create_model_client_from_config

from .contracts import TargetResolutionOutcome
from .logging_config import emit_tool_event, emit_tool_events
from .pipelines.automatic import run_automatic_pipeline
from .pipelines.interactive import (
    create_interactive_agent,
    create_user_proxy_agent,
    run_interactive_pipeline,
)
from .planner import (
    create_candidate_resolution_agent,
    create_target_auditor_agent,
    create_target_intent_agent,
    run_planner,
)
from .progressive import (
    ProgressivePreviewPublisher,
    artifact_key_for_pair,
    ensure_artifact_key,
    preview_from_item,
)
from .resolution import (
    fetch_cache_candidates,
    fetch_mask_inventory,
    resolve_voxtell_prompt,
    select_newest_matching_mask,
)
from .services import SegmentationService
from .utils import (
    MONAI_URL,
    check_having_mask,
    cleanup_discarded,
    download_and_pair_volumes,
    generate_gif_overlays,
)
from .ws_client_sender import send_ws_message_remote

logger = logging.getLogger("SegmentationModule")


def resolve_mcp_server_path() -> Path:
    return Path(__file__).resolve().parents[4] / "mcp" / "server.py"


class SegmentationModule:
    def __init__(
        self,
        config_path: str = "config.json",
        model_name: str = "gpt-oss:120b-cloud",
        session_id: str | None = None,
    ):
        self.config_path = config_path
        self.model_name = model_name
        self.session_id = session_id
        self.model_client = None
        self.discovery_tools = None
        self.UserProxyAgent = None
        self.UserFallbackAgent = None
        self.segmentation_service = None
        self.http_client: httpx.AsyncClient | None = None
        self.agent_base_url = os.environ.get(
            "MAS_BASE_URL", "http://localhost:8150"
        ).rstrip("/")

    async def initialize(self):
        self.model_client = create_model_client_from_config()

        server_path = resolve_mcp_server_path()
        if not server_path.exists():
            raise FileNotFoundError(f"MCP server not found at: {server_path}")
        env = os.environ.copy()
        env.update(
            {
                "SESSION_ID": self.session_id or "",
                "AGENT_ID": "segmentation_agent",
            }
        )
        server = StdioServerParams(
            command="python",
            args=[str(server_path)],
            read_timeout_seconds=2000,
            env=env,
        )
        tools = await mcp_server_tools(server)
        discovery_tools = [tool for tool in tools if tool.name == "list_models"]
        if not discovery_tools:
            raise RuntimeError("Segmentation agent requires the list_models tool")

        self.discovery_tools = discovery_tools
        # Retain the generic interactive abstractions for future models.
        self.UserFallbackAgent = create_interactive_agent(
            self.model_client, discovery_tools
        )
        self.UserProxyAgent = create_user_proxy_agent(self.model_client)
        self.http_client = httpx.AsyncClient()
        self.segmentation_service = SegmentationService(client=self.http_client)

    async def close(self) -> None:
        if self.http_client is not None:
            await self.http_client.aclose()
            self.http_client = None

    async def _check_having_mask(self, volume_uid: str):
        return await check_having_mask(volume_uid, client=self.http_client)

    @staticmethod
    def _load_run_context(
        input_data: Union[str, Dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(input_data, str):
            raise ValueError(
                "Segmentation requires the workspace config path so state can be loaded"
            )
        config_path = Path(input_data)
        state_path = config_path.with_name(
            config_path.name.replace("config", "state", 1)
        )
        with state_path.open("r", encoding="utf-8") as stream:
            state = json.load(stream)
        with config_path.open("r", encoding="utf-8") as stream:
            config = json.load(stream)
        return config, state

    @staticmethod
    def _build_instruction(state: dict[str, Any]) -> str:
        steps = state.get("plan", {}).get("steps", [])
        segmentation_step = next(
            (step for step in steps if step.get("module") == "segmentation_agent"),
            None,
        )
        if not segmentation_step:
            raise ValueError("Segmentation step not found")

        instructions = str(
            segmentation_step.get("task_message", {}).get("instructions") or ""
        ).strip()
        hypothesis = state.get("hypothesis", {})
        if not instructions:
            instructions = (
                "Perform ROI segmentation for the study: "
                f"'{hypothesis.get('title', '')}'."
            )

        context_parts = []
        for key, label in (
            ("hypothesis", "Hypothesis"),
            ("population", "Population"),
            ("clinical_context", "Clinical context"),
        ):
            value = hypothesis.get(key)
            if isinstance(value, dict):
                value = ", ".join(f"{name}: {item}" for name, item in value.items())
            if value:
                context_parts.append(f"{label}: {value}")
        if context_parts:
            instructions += "\n\n" + "\n".join(context_parts)
        return instructions

    @staticmethod
    def _model_for_target(plan, target_index: int) -> str:
        if target_index < len(plan.selected_models):
            return plan.selected_models[target_index]
        if len(plan.selected_models) == 1:
            return plan.selected_models[0]
        raise ValueError(
            f"No segmentation model selected for target index {target_index}"
        )

    async def _wait_for_exact_mask(
        self,
        volume_uid: str,
        segmentation_uid: str,
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any] | None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            records = await self._check_having_mask(volume_uid)
            for record in records:
                if (
                    str(record.get("label_series_instance_uid") or "")
                    == segmentation_uid
                ):
                    return record
            await asyncio.sleep(5)
        return None

    @staticmethod
    def _write_manifest(
        workspace_dir: str,
        resolution_outcome,
        work_items: list[dict[str, Any]],
        *,
        stage: str,
        run_id: str,
    ) -> Path:
        plan = resolution_outcome.plan
        manifest_path = Path(workspace_dir) / "segmentation_manifest.json"
        existing_created_at = None
        if manifest_path.exists():
            try:
                existing_created_at = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                ).get("created_at")
            except (OSError, json.JSONDecodeError):
                existing_created_at = None
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "run_id": run_id,
            "stage": stage,
            "created_at": existing_created_at or now,
            "updated_at": now,
            "chosen_path": plan.chosen_path,
            "resolution": {
                "policy_fingerprint": resolution_outcome.policy_fingerprint,
                "intent_plan": resolution_outcome.intent_plan.model_dump(),
                "candidate_plan": resolution_outcome.candidate_plan.model_dump(),
                "audit": resolution_outcome.audit.model_dump(),
                "final_plan": plan.model_dump(),
            },
            "targets": [
                {
                    "target_id": target.target_id,
                    "canonical_label": target.canonical_label,
                    "anatomy": target.anatomy,
                    "pathology": target.pathology,
                    "laterality": target.laterality,
                    "confidence": target.confidence,
                    "reason": target.reason,
                }
                for target in plan.targets
            ],
            "pairs": work_items,
        }
        temporary_path = manifest_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary_path.replace(manifest_path)
        return manifest_path

    def _create_publisher(
        self,
        plan,
        work_items: list[dict[str, Any]],
        workspace_dir: str,
    ) -> ProgressivePreviewPublisher:
        return ProgressivePreviewPublisher(
            plan=plan,
            work_items=work_items,
            workspace_dir=workspace_dir,
            session_id=self.session_id,
            client=self.http_client,
            base_url=self.agent_base_url,
        )

    def _persist_processing_state(
        self,
        workspace_dir: str,
        resolution_outcome: TargetResolutionOutcome,
        work_items: list[dict[str, Any]],
        run_id: str,
    ) -> Path:
        return self._write_manifest(
            workspace_dir,
            resolution_outcome,
            work_items,
            stage="processing",
            run_id=run_id,
        )

    async def _fail_pair(
        self,
        item: dict[str, Any],
        *,
        stage: str,
        message: str,
        details: dict[str, Any] | None,
        workspace_dir: str,
        resolution_outcome: TargetResolutionOutcome,
        work_items: list[dict[str, Any]],
        run_id: str,
        publisher: ProgressivePreviewPublisher,
    ) -> None:
        item.update(
            {
                "status": "failed",
                "error_stage": stage,
                "error": message,
                "error_details": details,
                "gif_path": None,
            }
        )
        self._persist_processing_state(
            workspace_dir, resolution_outcome, work_items, run_id
        )
        await publisher.publish_failure()

    @staticmethod
    def _workspace_volume_is_valid(workspace_dir: str, volume_uid: str) -> bool:
        path = Path(workspace_dir) / "volumes" / f"{volume_uid}.nii.gz"
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        try:
            image = sitk.ReadImage(str(path))
            return image.GetDimension() == 3 and all(size > 0 for size in image.GetSize())
        except Exception:
            logger.warning("Invalid cached workspace volume: %s", path, exc_info=True)
            return False

    async def _prepare_pair_preview(
        self,
        item: dict[str, Any],
        *,
        workspace_dir: str,
        resolution_outcome: TargetResolutionOutcome,
        work_items: list[dict[str, Any]],
        run_id: str,
        publisher: ProgressivePreviewPublisher,
        allow_reuse_fallback: bool = False,
    ) -> bool:
        item["status"] = "preparing"
        self._persist_processing_state(
            workspace_dir, resolution_outcome, work_items, run_id
        )
        artifact_key = ensure_artifact_key(item)
        successful, skipped = await download_and_pair_volumes(
            workspace_dir,
            [
                {
                    "pair_id": item["pair_id"],
                    "artifact_key": artifact_key,
                    "volume_uid": item["volume_uid"],
                    "target_id": item["target_id"],
                    "label_uid": item["source_segmentation_uid"],
                }
            ],
            download_volumes=not self._workspace_volume_is_valid(
                workspace_dir, item["volume_uid"]
            ),
            task_name=f"{item['model']}_segmentation_task",
            client=self.http_client,
        )
        pair_result = next(
            (result for result in successful if result.get("pair_id") == item["pair_id"]),
            None,
        )
        if pair_result is None:
            can_fallback = allow_reuse_fallback and any(
                skipped_item.get("reason") == "normalization_failed"
                for skipped_item in skipped
            )
            if can_fallback:
                item.update(
                    {
                        "reused": False,
                        "source_segmentation_uid": None,
                        "status": "pending",
                        "mask_path": None,
                        "gif_path": None,
                        "error_stage": None,
                        "error": None,
                        "error_details": None,
                    }
                )
                self._persist_processing_state(
                    workspace_dir, resolution_outcome, work_items, run_id
                )
                return False
            await self._fail_pair(
                item,
                stage="mask_export",
                message="Mask download or normalization failed",
                details={"skipped": skipped},
                workspace_dir=workspace_dir,
                resolution_outcome=resolution_outcome,
                work_items=work_items,
                run_id=run_id,
                publisher=publisher,
            )
            return False

        item["mask_path"] = f"masks/{artifact_key}.nii.gz"
        item["geometry_adjusted"] = bool(
            (item.get("inference_result") or {}).get("geometry_adjusted")
            or pair_result.get("geometry_adjusted")
        )
        item["geometry"] = pair_result.get("geometry")

        previews = await asyncio.to_thread(
            generate_gif_overlays,
            workspace_dir,
            [item],
        )
        preview = previews[0] if previews else None
        if preview is None:
            await self._fail_pair(
                item,
                stage="gif_generation",
                message="Mask was prepared but its GIF preview could not be generated",
                details=None,
                workspace_dir=workspace_dir,
                resolution_outcome=resolution_outcome,
                work_items=work_items,
                run_id=run_id,
                publisher=publisher,
            )
            return False

        if preview.get("isEmptyMask"):
            item.update(
                {
                    "status": "ready",
                    "gif_path": preview.get("gifPath") or f"overlays/{artifact_key}.gif",
                    "is_empty_mask": True,
                    "warning": "Volume mask is empty; modify it before saving if needed",
                    "error_stage": None,
                    "error": None,
                    "error_details": None,
                }
            )
            self._persist_processing_state(
                workspace_dir, resolution_outcome, work_items, run_id
            )
            await publisher.publish_ready()
            return True

        item.update(
            {
                "status": "ready",
                "gif_path": preview.get("gifPath") or f"overlays/{artifact_key}.gif",
                "is_empty_mask": False,
                "warning": None,
                "error_stage": None,
                "error": None,
                "error_details": None,
            }
        )
        self._persist_processing_state(
            workspace_dir, resolution_outcome, work_items, run_id
        )
        await publisher.publish_ready()
        return True

    async def _execute_work_items(
        self,
        workspace_dir: str,
        resolution_outcome: TargetResolutionOutcome,
        work_items: list[dict[str, Any]],
        run_id: str,
        publisher: ProgressivePreviewPublisher,
    ) -> None:
        plan = resolution_outcome.plan
        ordered = sorted(work_items, key=lambda item: int(item["sequence_index"]))
        item_by_pair = {item["pair_id"]: item for item in ordered}

        for item in ordered:
            if item.get("status") != "ready":
                continue
            if preview_from_item(item, workspace_dir, self.agent_base_url) is None:
                item["status"] = (
                    "preparing" if item.get("source_segmentation_uid") else "failed"
                )
                if item["status"] == "failed":
                    item["error_stage"] = "checkpoint_artifacts"
                    item["error"] = "Ready checkpoint is missing its preview artifacts"

        # Reused masks are made visible before any new inference begins.
        reused_count = sum(1 for item in ordered if item.get("reused") and item.get("status") not in {"ready", "failed"})
        if reused_count:
            await emit_tool_events(
                event_types=["tool_started", "tool_finished"],
                content=TextPayload(message=f"Sending {reused_count} existing segmentation mask(s) to frontend."),
                session_id=self.session_id,
            )
        for item in ordered:
            if not item.get("reused") or item.get("status") in {"ready", "failed"}:
                continue
            item["status"] = "reusing"
            self._persist_processing_state(
                workspace_dir, resolution_outcome, work_items, run_id
            )
            await self._prepare_pair_preview(
                item,
                workspace_dir=workspace_dir,
                resolution_outcome=resolution_outcome,
                work_items=work_items,
                run_id=run_id,
                publisher=publisher,
                allow_reuse_fallback=True,
            )

        # Resume pairs whose inference UID was persisted before interruption.
        for item in ordered:
            if item.get("status") not in {"inference_succeeded", "preparing"}:
                continue
            if not item.get("source_segmentation_uid"):
                item["status"] = "pending"
                continue
            await self._prepare_pair_preview(
                item,
                workspace_dir=workspace_dir,
                resolution_outcome=resolution_outcome,
                work_items=work_items,
                run_id=run_id,
                publisher=publisher,
            )

        for item in ordered:
            if item.get("status") == "inferencing":
                item["status"] = "pending"
        self._persist_processing_state(
            workspace_dir, resolution_outcome, work_items, run_id
        )

        if any(item.get("status") == "ready" for item in ordered):
            await publisher.publish_ready()

        pending = [item for item in ordered if item.get("status") == "pending"]

        async def on_pair_started(event: dict[str, Any]) -> None:
            item = item_by_pair[event["pair_id"]]
            item["status"] = "inferencing"
            self._persist_processing_state(
                workspace_dir, resolution_outcome, work_items, run_id
            )

        async def on_pair_completed(outcome: dict[str, Any]) -> None:
            item = item_by_pair[outcome["pair_id"]]
            try:
                if outcome["status"] == "failed":
                    failure = outcome.get("failure") or {}
                    await self._fail_pair(
                        item,
                        stage=str(failure.get("stage") or "inference"),
                        message=str(failure.get("message") or "Segmentation inference failed"),
                        details={
                            key: value
                            for key, value in failure.items()
                            if key not in {"pair_id", "stage", "message"}
                        }
                        or None,
                        workspace_dir=workspace_dir,
                        resolution_outcome=resolution_outcome,
                        work_items=work_items,
                        run_id=run_id,
                        publisher=publisher,
                    )
                    return

                inference_result = outcome.get("inference_result") or {}
                segmentation_uid = str(
                    inference_result.get("segmentation_series_uid") or ""
                )
                if not segmentation_uid:
                    await self._fail_pair(
                        item,
                        stage="inference_result",
                        message="Inference returned no segmentation SeriesInstanceUID",
                        details={"model": item["model"], "prompt": item["inference_prompt"]},
                        workspace_dir=workspace_dir,
                        resolution_outcome=resolution_outcome,
                        work_items=work_items,
                        run_id=run_id,
                        publisher=publisher,
                    )
                    return

                item["inference_result"] = {
                    key: inference_result.get(key)
                    for key in (
                        "segmentation_series_uid",
                        "orthanc_instance_id",
                        "sync_succeeded",
                        "retry_count",
                        "status_code",
                        "geometry_adjusted",
                        "original_mask_shape",
                        "final_mask_shape",
                    )
                }
                item["source_segmentation_uid"] = segmentation_uid
                item["status"] = "inference_succeeded"
                self._persist_processing_state(
                    workspace_dir, resolution_outcome, work_items, run_id
                )

                record = await self._wait_for_exact_mask(
                    item["volume_uid"], segmentation_uid
                )
                if not record:
                    await self._fail_pair(
                        item,
                        stage="segmentation_sync",
                        message=(
                            "Timed out waiting for exact generated segmentation UID "
                            f"{segmentation_uid}"
                        ),
                        details={"segmentation_series_uid": segmentation_uid},
                        workspace_dir=workspace_dir,
                        resolution_outcome=resolution_outcome,
                        work_items=work_items,
                        run_id=run_id,
                        publisher=publisher,
                    )
                    return

                item["source_method"] = record.get("method")
                item["source_model"] = (
                    record.get("name") or record.get("model_name") or item["model"]
                )
                item["source_description"] = record.get("segmentation_type")
                await self._prepare_pair_preview(
                    item,
                    workspace_dir=workspace_dir,
                    resolution_outcome=resolution_outcome,
                    work_items=work_items,
                    run_id=run_id,
                    publisher=publisher,
                )
            except Exception as exc:
                logger.exception("Pair completion failed for %s", item["pair_id"])
                await self._fail_pair(
                    item,
                    stage="preview_callback",
                    message=str(exc),
                    details={"exception_type": type(exc).__name__},
                    workspace_dir=workspace_dir,
                    resolution_outcome=resolution_outcome,
                    work_items=work_items,
                    run_id=run_id,
                    publisher=publisher,
                )

        if pending:
            await emit_tool_events(
                event_types=["tool_started", "tool_finished"],
                content=TextPayload(message=f"Starting Segmentation on {len(pending)} volume(s)."),
                session_id=self.session_id,
            )
            if plan.chosen_path == "automatic":
                await run_automatic_pipeline(
                    pending,
                    self.segmentation_service,
                    self.session_id,
                    on_pair_started=on_pair_started,
                    on_pair_completed=on_pair_completed,
                )
            else:
                await run_interactive_pipeline(
                    self.UserFallbackAgent,
                    self.UserProxyAgent,
                    pending,
                    self.segmentation_service,
                    self.session_id,
                    on_pair_started=on_pair_started,
                    on_pair_completed=on_pair_completed,
                )

        for item in ordered:
            if item.get("status") in {"ready", "failed"}:
                continue
            await self._fail_pair(
                item,
                stage="pair_state",
                message=(
                    "Segmentation pair did not reach a terminal ready or failed state"
                ),
                details={"last_status": item.get("status")},
                workspace_dir=workspace_dir,
                resolution_outcome=resolution_outcome,
                work_items=work_items,
                run_id=run_id,
                publisher=publisher,
            )

    async def _wait_for_review(self) -> dict[str, list[str]]:
        try:
            await send_ws_message_remote(
                "agent_waiting_for_user_input",
                {
                    "agent": "segmentation_agent",
                    "status": "WAITING_FOR_REVIEW",
                },
                self.session_id,
                client=self.http_client,
                base_url=self.agent_base_url,
                timeout=5.0,
            )
        except httpx.HTTPError as exc:
            logger.warning("Could not publish review waiting state: %s", exc)

        retry_delay = 0.5
        while True:
            try:
                response = await self.http_client.get(
                    f"{self.agent_base_url}/segmentation-review/wait/"
                    f"{self.session_id}",
                    timeout=3610.0,
                )
                if response.status_code == 408:
                    await asyncio.sleep(retry_delay)
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                logger.warning("Review wait connection interrupted: %s", exc)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 10.0)

    @staticmethod
    def _normalise_review_keys(
        review: dict[str, Any],
        work_items: list[dict[str, Any]],
        target_count: int,
    ) -> dict[str, set[str]]:
        pair_ids = {item["pair_id"] for item in work_items}
        pair_by_volume = {
            item["volume_uid"]: item["pair_id"] for item in work_items
        }

        def convert(values) -> set[str]:
            converted = set()
            for value in values or []:
                key = str(value)
                if key in pair_ids:
                    converted.add(key)
                elif target_count == 1 and key in pair_by_volume:
                    converted.add(pair_by_volume[key])
            return converted

        return {
            "accepted": convert(review.get("accepted")),
            "rejected": convert(review.get("rejected")),
            "modified": convert(review.get("modified")),
        }

    @staticmethod
    def _load_checkpoint(
        workspace_dir: str,
        datasetlist: list[str],
    ) -> tuple[
        TargetResolutionOutcome,
        list[dict[str, Any]],
        str,
        str,
    ] | None:
        manifest_path = Path(workspace_dir) / "segmentation_manifest.json"
        if not manifest_path.exists():
            return None
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            stage = str(payload.get("stage") or "")
            if stage not in {
                "processing",
                "awaiting_review",
                "reviewed",
            }:
                return None
            work_items = payload.get("pairs")
            if not isinstance(work_items, list) or not work_items:
                return None
            if {str(item.get("volume_uid")) for item in work_items} != set(
                datasetlist
            ):
                return None

            seen_pair_ids: set[str] = set()
            seen_sequence_indexes: set[int] = set()
            workspace = Path(workspace_dir).resolve()
            for fallback_index, item in enumerate(work_items):
                pair_id = str(item.get("pair_id") or "")
                if not pair_id or pair_id in seen_pair_ids:
                    raise ValueError(f"Invalid or duplicate pair_id: {pair_id!r}")
                seen_pair_ids.add(pair_id)
                item.setdefault("artifact_key", artifact_key_for_pair(pair_id))
                sequence_index = int(item.get("sequence_index", fallback_index))
                if sequence_index in seen_sequence_indexes:
                    raise ValueError(
                        f"Duplicate segmentation sequence_index: {sequence_index}"
                    )
                seen_sequence_indexes.add(sequence_index)
                item["sequence_index"] = sequence_index
                item.setdefault("inference_result", None)
                item.setdefault("gif_path", None)
                item.setdefault("reuse_candidate_uid", None)
                if item.get("mask_path"):
                    mask_path = (workspace / str(item["mask_path"])).resolve()
                    mask_path.relative_to(workspace)
                if item.get("gif_path"):
                    gif_path = (workspace / str(item["gif_path"])).resolve()
                    gif_path.relative_to(workspace)
                if item.get("status") == "resolved":
                    item["status"] = "reusing" if item.get("reused") else "preparing"

            resolution = payload["resolution"]
            outcome = TargetResolutionOutcome.model_validate(
                {
                    "plan": resolution["final_plan"],
                    "intent_plan": resolution["intent_plan"],
                    "candidate_plan": resolution["candidate_plan"],
                    "audit": resolution["audit"],
                    "policy_fingerprint": resolution["policy_fingerprint"],
                }
            )
            return (
                outcome,
                work_items,
                str(payload["run_id"]),
                stage,
            )
        except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring invalid segmentation checkpoint: %s", exc)
            return None

    @staticmethod
    def _build_result(
        manifest_path: Path,
        plan,
        work_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        failures = [
            item for item in work_items if item["status"] in {"failed", "rejected"}
        ]
        accepted_count = sum(
            item["review_status"] in {"accepted", "modified"}
            for item in work_items
        )
        successful_count = len(work_items) - len(failures)
        if not failures:
            status = "completed"
        elif successful_count:
            status = "partial"
        else:
            status = "failed"
        return {
            "status": status,
            "manifest_path": str(manifest_path),
            "segmentation_method": (
                f"{plan.chosen_path}: {', '.join(plan.selected_models)}"
            ),
            "targets": plan.target_labels,
            "pair_count": len(work_items),
            "ready_count": sum(item["status"] == "ready" for item in work_items),
            "accepted_count": accepted_count,
            "reused_count": sum(bool(item["reused"]) for item in work_items),
            "generated_count": sum(
                not item["reused"] and item["source_segmentation_uid"] is not None
                for item in work_items
            ),
            "failures": [
                {
                    "pair_id": item["pair_id"],
                    "stage": item.get("error_stage"),
                    "error": item.get("error") or item["status"],
                    "details": item.get("error_details"),
                }
                for item in failures
            ],
        }

    async def _complete_review(
        self,
        workspace_dir: str,
        resolution_outcome: TargetResolutionOutcome,
        work_items: list[dict[str, Any]],
        run_id: str,
        publisher: ProgressivePreviewPublisher | None = None,
    ) -> dict[str, Any]:
        plan = resolution_outcome.plan
        publisher = publisher or self._create_publisher(
            plan, work_items, workspace_dir
        )
        manifest_path = self._write_manifest(
            workspace_dir,
            resolution_outcome,
            work_items,
            stage="awaiting_review",
            run_id=run_id,
        )
        ready_items = [item for item in work_items if item["status"] == "ready"]
        await publisher.publish_final()
        if ready_items:
            raw_review = await self._wait_for_review()
            review = self._normalise_review_keys(
                raw_review,
                work_items,
                len(plan.targets),
            )
            ready_pair_ids = {item["pair_id"] for item in ready_items}
            review["accepted"] -= review["modified"]
            review["rejected"] -= review["modified"] | review["accepted"]
            reviewed_pair_ids = (
                review["modified"] | review["accepted"] | review["rejected"]
            )
            review["rejected"].update(ready_pair_ids - reviewed_pair_ids)

            accepted_or_modified = review["accepted"] | review["modified"]
            cleanup_discarded(
                workspace_dir,
                list(review["accepted"]),
                list(review["modified"]),
                {
                    item["pair_id"]: ensure_artifact_key(item)
                    for item in work_items
                },
            )
            for item in work_items:
                pair_id = item["pair_id"]
                artifact_key = ensure_artifact_key(item)
                if pair_id in review["modified"]:
                    temp_path = (
                        Path(workspace_dir)
                        / "temp_segmentations"
                        / f"seg_{artifact_key}_modified.nii.gz"
                    )
                    target_path = (
                        Path(workspace_dir) / "masks" / f"{artifact_key}.nii.gz"
                    )
                    if temp_path.exists():
                        shutil.copy2(temp_path, target_path)
                    item["is_empty_mask"] = False
                    item["warning"] = None
                    item["review_status"] = "modified"
                elif pair_id in review["accepted"]:
                    item["review_status"] = "accepted"
                elif pair_id in review["rejected"]:
                    item["review_status"] = "rejected"
                    item["status"] = "rejected"

            if not accepted_or_modified:
                logger.warning("Human review accepted no segmentation masks")

        self._write_manifest(
            workspace_dir,
            resolution_outcome,
            work_items,
            stage="reviewed",
            run_id=run_id,
        )
        result = self._build_result(manifest_path, plan, work_items)
        await emit_tool_events(
            event_types=["tool_started", "tool_finished"],
            content=TextPayload(message="Segmentation is done."),
            session_id=self.session_id,
        )
        await emit_tool_event(
            event_type="tool_finished",
            content=TextPayload(
                message=(
                    f"Segmentation {result['status']}: "
                    f"{result['accepted_count']}/{result['pair_count']} "
                    "segmentation masks accepted."
                )
            ),
            session_id=self.session_id,
        )
        return result

    async def run(
        self,
        input_data: Union[str, Dict[str, Any]],
        workspace_dir: str,
    ):
        if (
            not self.model_client
            or not self.discovery_tools
            or not self.segmentation_service
        ):
            raise RuntimeError("SegmentationModule.initialize() must be called first")

        config, state = self._load_run_context(input_data)
        datasetlist: List[str] = config.get("datasetlist", [])
        if not datasetlist:
            raise ValueError(
                "The datasetlist is empty. The population planner did not "
                "provide eligible volumes."
            )
        unsafe_volume_uids = [
            str(uid)
            for uid in datasetlist
            if not str(uid)
            or any(character in str(uid) for character in '/\\<>:"|?*')
            or any(ord(character) < 32 for character in str(uid))
        ]
        if unsafe_volume_uids:
            raise ValueError(
                "Dataset contains unsafe SeriesInstanceUID values: "
                f"{unsafe_volume_uids}"
            )
        checkpoint = self._load_checkpoint(workspace_dir, datasetlist)
        if checkpoint is not None:
            (
                resolution_outcome,
                work_items,
                run_id,
                checkpoint_stage,
            ) = checkpoint
            manifest_path = Path(workspace_dir) / "segmentation_manifest.json"
            if checkpoint_stage == "reviewed":
                logger.info("Resuming completed segmentation review checkpoint")
                return self._build_result(
                    manifest_path,
                    resolution_outcome.plan,
                    work_items,
                )
            publisher = self._create_publisher(
                resolution_outcome.plan, work_items, workspace_dir
            )
            if checkpoint_stage == "processing":
                logger.info("Resuming progressive segmentation processing checkpoint")
            else:
                logger.info("Resuming segmentation from awaiting-review checkpoint")
            await self._execute_work_items(
                workspace_dir,
                resolution_outcome,
                work_items,
                run_id,
                publisher,
            )
            return await self._complete_review(
                workspace_dir,
                resolution_outcome,
                work_items,
                run_id,
                publisher,
            )

        instructions = self._build_instruction(state)
        run_id = str(uuid4())

        await emit_tool_events(
            event_types=["tool_started", "tool_progress"],
            content=TextPayload(message="Resolving segmentation targets..."),
            session_id=self.session_id,
        )

        # A failed existing-mask request is fatal by design: silently assuming
        # no masks would create duplicate segmentations.
        mask_inventory = await fetch_mask_inventory(
            datasetlist, self._check_having_mask
        )
        cache_candidates = await fetch_cache_candidates(
            MONAI_URL,
            client=self.http_client,
        )

        # Construct fresh stage agents for every run. AssistantAgent does not
        # expose reset(), and fresh instances guarantee that
        # neither earlier runs nor another stage's conversation can influence
        # the current clinical target decision.
        intent_agent = create_target_intent_agent(self.model_client)
        candidate_agent = create_candidate_resolution_agent(
            self.model_client,
            self.discovery_tools,
        )
        auditor_agent = create_target_auditor_agent(self.model_client)
        resolution_outcome = await run_planner(
            intent_agent,
            candidate_agent,
            auditor_agent,
            instructions,
            mask_inventory.candidates,
            cache_candidates,
        )
        plan = resolution_outcome.plan

        # ── Early announcement ──────────────────────────────────────────────
        # Push the resolved target ROI and model to the sidebar card
        # immediately, before any mask processing begins.
        try:
            target_labels = [t.canonical_label for t in plan.targets if t.canonical_label]
            selected_models = list(plan.selected_models)
            await send_ws_message_remote(
                "segmentation_output",
                {
                    "runId": run_id,
                    "targetRoi": ", ".join(target_labels),
                    "targets": [
                        {"targetId": t.target_id, "canonicalLabel": t.canonical_label}
                        for t in plan.targets
                    ],
                    "selectedModel": ", ".join(str(m) for m in selected_models),
                    "volumes": [],
                    "failedVolumes": [],
                    "progress": {"total": len(datasetlist), "generated": 0, "failed": 0},
                    "isFinished": False,
                },
                self.session_id,
                client=self.http_client,
                base_url=self.agent_base_url,
                timeout=10.0,
            )
        except Exception:
            logger.warning("Could not send early segmentation target announcement", exc_info=True)
        # ────────────────────────────────────────────────────────────────────

        work_items: list[dict[str, Any]] = []
        for target_index, target in enumerate(plan.targets):
            prompt, cache_hit = resolve_voxtell_prompt(
                target, cache_candidates
            )
            model = self._model_for_target(plan, target_index)
            for volume_uid in datasetlist:
                pair_id = f"{volume_uid}__{target.target_id}"
                artifact_key = artifact_key_for_pair(pair_id)
                existing = select_newest_matching_mask(
                    volume_uid, target, mask_inventory
                )
                work_items.append(
                    {
                        "pair_id": pair_id,
                        "artifact_key": artifact_key,
                        "sequence_index": -1,
                        "run_id": run_id,
                        "correlation_id": f"{run_id}:{pair_id}",
                        "volume_uid": volume_uid,
                        "target_id": target.target_id,
                        "canonical_label": target.canonical_label,
                        "inference_prompt": prompt,
                        "cache_hit": cache_hit,
                        "model": model,
                        "reuse_candidate_uid": (
                            existing.get("label_series_instance_uid")
                            if existing
                            else None
                        ),
                        "source_segmentation_uid": (
                            existing.get("label_series_instance_uid")
                            if existing
                            else None
                        ),
                        "source_method": existing.get("method") if existing else None,
                        "source_model": (
                            (existing.get("name") or existing.get("model_name"))
                            if existing
                            else None
                        ),
                        "source_description": (
                            existing.get("segmentation_type") if existing else None
                        ),
                        "reused": bool(existing),
                        "status": "reusing" if existing else "pending",
                        "inference_result": None,
                        "review_status": "pending",
                        "mask_path": None,
                        "gif_path": None,
                        "is_empty_mask": False,
                        "warning": None,
                        "geometry_adjusted": False,
                        "geometry": None,
                        "error_stage": None,
                        "error": None,
                        "error_details": None,
                    }
                )

        work_items = [
            *[item for item in work_items if item["reused"]],
            *[item for item in work_items if not item["reused"]],
        ]
        for sequence_index, item in enumerate(work_items):
            item["sequence_index"] = sequence_index
        if len({item["pair_id"] for item in work_items}) != len(work_items):
            raise ValueError("Segmentation work items contain duplicate pair_id values")
        self._persist_processing_state(
            workspace_dir, resolution_outcome, work_items, run_id
        )
        publisher = self._create_publisher(plan, work_items, workspace_dir)

        target_count = len(plan.targets)
        volume_count = len(datasetlist)
        target_summary = (
            "Single target label"
            if target_count == 1
            else f"Multiple target labels ({target_count})"
        )
        volume_summary = (
            "1 volume"
            if volume_count == 1
            else f"{volume_count} volumes"
        )
        await emit_tool_event(
            event_type="tool_finished",
            content=TextPayload(
                message=f"{target_summary} for {volume_summary}."
            ),
            session_id=self.session_id,
        )

        existing_mask_pair_count = sum(bool(item["reused"]) for item in work_items)
        await emit_tool_events(
            event_types=["tool_started", "tool_finished"],
            content=TextPayload(
                message=(
                    f"Found {existing_mask_pair_count} existing segmentation masks"
                )
            ),
            session_id=self.session_id,
        )

        await self._execute_work_items(
            workspace_dir,
            resolution_outcome,
            work_items,
            run_id,
            publisher,
        )
        return await self._complete_review(
            workspace_dir,
            resolution_outcome,
            work_items,
            run_id,
            publisher,
        )
