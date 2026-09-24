"""Segmentation orchestration with agent-resolved clinical target identity."""

import asyncio
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from storage_paths import workspace_subpath
from typing import Any
from uuid import uuid4

import httpx
import SimpleITK as sitk
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools

from agent_system.agents.clinical_roi_policy import (
    canonicalize_voxtell_prompt_instructions,
)
from agent_system.models.tool_logging import TextPayload
from services.timeout_policy import MCP_STANDARD_READ_SECONDS
from client_config import create_model_client_from_config

from .clinical_target_policy import (
    CLINICAL_TARGET_POLICY_FINGERPRINT,
)
from .contracts import CURRENT_SCHEMA_VERSION, TargetResolutionOutcome
from .logging_config import emit_tool_event, emit_tool_events
from .pipelines.automatic import run_automatic_pipeline
from .pipelines.interactive import (
    create_interactive_agent,
    create_user_proxy_agent,
    run_interactive_pipeline,
)
from .planner import (
    create_candidate_resolution_agent,
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
from services.artifact_client import register_render, render_logical_key
from .batch_store import (
    BatchReconciliationRequired,
    SegmentationBatchStore,
    batch_config,
    batch_targets,
    work_items_from_batch,
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

from services.mask_store import MaskStore, SegRef, orthanc_first

logger = logging.getLogger("SegmentationModule")


def resolve_mcp_server_path() -> Path:
    return Path(__file__).resolve().parents[4] / "mcp" / "server.py"



class SegmentationModule:
    def __init__(
        self,
        model_name: str = "gpt-oss:120b-cloud",
        session_id: str | None = None,
        agent_run_id: str | None = None,
    ):
        self.model_name = model_name
        self.session_id = session_id
        # The run this module's work belongs to: identity is the step's
        # run, never the agent name.
        self.agent_run_id = agent_run_id
        # current this flow: the checkpoint is the `segmentation_runs` batch.
        self.batch_store: SegmentationBatchStore | None = None
        self.segmentation_run_id: str | None = None
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
                # Passed down so any tool this module reaches writes to the
                # same `agent_runs` row.
                "AGENT_RUN_ID": self.agent_run_id or os.environ.get("AGENT_RUN_ID", ""),
                "STEP_ID": os.environ.get("STEP_ID", ""),
            }
        )
        server = StdioServerParams(
            command="python",
            args=[str(server_path)],
            read_timeout_seconds=MCP_STANDARD_READ_SECONDS,
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

    async def _load_run_context(self) -> tuple[list[str], str, dict[str, Any]]:
        """What this step segments and why — read from PostgreSQL."""
        from clients.backend_client import BackendClient

        cohort = await BackendClient.get_population_cohort(str(self.session_id))
        if not cohort:
            raise ValueError(
                "The population planner has not recorded a cohort for this session."
            )
        if not cohort.get("frozen_at"):
            raise ValueError(
                "The population cohort has not been approved. Segmentation runs only on "
                "an approved cohort."
            )
        rows = await BackendClient.get_cohort_analysis_rows(str(cohort["cohort_id"]))
        datasetlist = [str(row["volume_series_instance_uid"]) for row in rows]

        run = await BackendClient.get_agent_run(str(self.agent_run_id)) or {}
        step = run.get("step") or {}
        instructions = step.get("instructions") or {}
        task_message = dict(instructions.get("task_message") or {})
        if not task_message.get("instructions") and instructions.get("description"):
            task_message["instructions"] = instructions.get("description")
        session = await BackendClient.get_session(str(self.session_id)) or {}
        intent = session.get("intent") or {}
        state = {
            "plan": {
                "steps": [
                    {
                        "module": step.get("agent_type") or "segmentation_agent",
                        "task_message": task_message,
                    }
                ]
            },
            "hypothesis": {
                "title": intent.get("title") or session.get("title") or "",
                "hypothesis": intent.get("statement") or session.get("prompt") or "",
                "population": intent.get("population") or "",
                "clinical_context": intent.get("clinical_context") or "",
                "roi_interpretation": intent.get("roi_interpretation"),
            },
        }
        return datasetlist, str(cohort["cohort_id"]), state

    @staticmethod
    def _build_instruction(
        state: dict[str, Any],
    ) -> tuple[str, dict | None]:
        """Return (instruction_text, locked_roi_interpretation)."""
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
        instructions = canonicalize_voxtell_prompt_instructions(instructions)
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

        # Extract the structured ROI lock produced by the Clinical Planner.
        # This may be stored directly on the hypothesis or on the step context.
        roi_interpretation: dict | None = None
        raw_roi = hypothesis.get("roi_interpretation") or (
            segmentation_step.get("task_message", {}).get("roi_interpretation")
        )
        if isinstance(raw_roi, dict) and raw_roi:
            roi_interpretation = raw_roi

        return instructions, roi_interpretation

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
        timeout_seconds: float = 600.0,
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

    async def _write_manifest(
        self,
        workspace_dir: str,
        resolution_outcome,
        work_items: list[dict[str, Any]],
        *,
        stage: str,
        run_id: str,
    ) -> str:
        """Checkpoint every pair onto its `segmentation_pairs` row."""
        if self.batch_store is None or not self.segmentation_run_id:
            raise RuntimeError("The segmentation batch has not been created yet")
        await self.batch_store.sync(self.segmentation_run_id, work_items, stage=stage)
        return self.segmentation_run_id

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

    async def _persist_processing_state(
        self,
        workspace_dir: str,
        resolution_outcome: TargetResolutionOutcome,
        work_items: list[dict[str, Any]],
        run_id: str,
    ) -> str:
        return await self._write_manifest(
            workspace_dir,
            resolution_outcome,
            work_items,
            stage="processing",
            run_id=run_id,
        )

    async def _resolve_mask_path(
        self,
        item: dict[str, Any],
        artifact_key: str,
    ) -> str:
        """Logical workspace path the rest of the pipeline reads this mask from.

        With ``MASK_SOURCE=orthanc`` the archived DICOM SEG is materialised into
        the session cache and used instead of the workspace copy, so previews and
        radiomics see exactly what the archive holds. Anything unexpected falls
        back to the local copy, which is what happens with the flag off.
        """
        local_path = f"masks/{artifact_key}.nii.gz"
        if not orthanc_first():
            return local_path

        seg_uid = str(
            (item.get("inference_result") or {}).get("segmentation_series_uid") or ""
        )
        volume_uid = str(item.get("volume_uid") or "")
        if not seg_uid or not volume_uid:
            return local_path

        try:
            async with MaskStore() as store:
                await store.ensure_local(
                    self.session_id,
                    SegRef(
                        seg_series_uid=seg_uid,
                        volume_uid=volume_uid,
                        target_label=item.get("canonical_label"),
                    ),
                )
            return f"mask_cache/{seg_uid}.nii.gz"
        except Exception:  # noqa: BLE001 - the local copy is a valid fallback
            logger.exception(
                "Could not read mask %s from Orthanc for pair %s; using the "
                "workspace copy instead",
                seg_uid,
                item.get("pair_id"),
            )
            return local_path

    async def _publish_reviewed_mask(
        self,
        item: dict[str, Any],
        mask_path: Path,
    ) -> None:
        """Archive a clinician's edited mask in Orthanc as a new DICOM SEG.

        The edit is published as its own series that references the model's
        segmentation, rather than replacing it: DICOM has no update, and the
        original is what the correction has to remain comparable against.

        A failure here is recorded on the item and does not stop the run. The
        local copy still exists, so the worst case is the behaviour that existed
        before this call -- except now it is visible instead of silent.
        """
        volume_uid = str(item.get("volume_uid") or "")
        target_label = str(
            item.get("canonical_label") or item.get("target_id") or ""
        ).strip()
        if not volume_uid or not target_label:
            item["orthanc_publish"] = "skipped"
            logger.warning(
                "Cannot archive edited mask for pair %s: missing volume or target",
                item.get("pair_id"),
            )
            return

        derived_from = (item.get("inference_result") or {}).get(
            "segmentation_series_uid"
        )
        try:
            async with MaskStore() as store:
                ref = await store.publish(
                    volume_uid=volume_uid,
                    target_label=target_label,
                    mask_path=Path(mask_path),
                    method="manual",
                    derived_from=derived_from,
                )
            item["manual_segmentation_series_uid"] = ref.seg_series_uid
            item["orthanc_publish"] = "ok"
            logger.info(
                "Archived edited mask for pair %s as %s",
                item.get("pair_id"),
                ref.seg_series_uid,
            )
            if orthanc_first():
                # Downstream must now read the edit that was just archived,
                # not the model's mask it was derived from.
                async with MaskStore() as store:
                    await store.ensure_local(self.session_id, ref)
                item["mask_path"] = f"mask_cache/{ref.seg_series_uid}.nii.gz"
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            item["orthanc_publish"] = "failed"
            item["orthanc_publish_error"] = str(exc)
            logger.exception(
                "Failed to archive edited mask for pair %s in Orthanc",
                item.get("pair_id"),
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
        await self._persist_processing_state(
            workspace_dir, resolution_outcome, work_items, run_id
        )
        await publisher.publish_failure()

    @staticmethod
    def _workspace_volume_is_valid(workspace_dir: str, volume_uid: str) -> bool:
        path = workspace_subpath(workspace_dir, f"volumes/{volume_uid}.nii.gz")
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
        await self._persist_processing_state(
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
                    "label_uid": item.get("source_segmentation_uid"),
                    "is_empty_mask": bool(item.get("is_empty_mask")),
                    "reused": bool(item.get("reused")),
                }
            ],
            download_volumes=not self._workspace_volume_is_valid(
                workspace_dir, item["volume_uid"]
            ),
            task_name=f"{item['model']}_segmentation_task",
            client=self.http_client,
            session_id=self.session_id,
        )
        pair_result = next(
            (result for result in successful if result.get("pair_id") == item["pair_id"]),
            None,
        )
        if pair_result is None:
            pair_failure = next(
                (
                    skipped_item
                    for skipped_item in skipped
                    if skipped_item.get("pair_id") == item["pair_id"]
                ),
                skipped[0] if skipped else {},
            )
            reason_code = str(pair_failure.get("reason") or "pair_preparation_failed")
            reusable_mask_failure = reason_code in {
                "mask_export_failed",
                "mask_file_missing",
                "normalization_failed",
            }
            can_fallback = (
                allow_reuse_fallback
                and reusable_mask_failure
                and self._workspace_volume_is_valid(
                    workspace_dir,
                    item["volume_uid"],
                )
            )
            if can_fallback:
                logger.warning(
                    "Existing mask %s could not be prepared for pair %s (%s); "
                    "falling back to fresh %s inference",
                    item.get("source_segmentation_uid"),
                    item["pair_id"],
                    reason_code,
                    item["model"],
                )
                item.update(
                    {
                        "reused": False,
                        "reuse_candidate_uid": item.get("source_segmentation_uid"),
                        "source_segmentation_uid": None,
                        "candidate_confirmed": False,
                        "source_method": None,
                        "source_model": None,
                        "source_description": None,
                        "status": "pending",
                        "mask_path": None,
                        "gif_path": None,
                        "reuse_fallback": {
                            "reason": reason_code,
                            "details": pair_failure,
                        },
                        "error_stage": None,
                        "error": None,
                        "error_details": None,
                    }
                )
                await self._persist_processing_state(
                    workspace_dir, resolution_outcome, work_items, run_id
                )
                return False

            failure_map = {
                "volume_export_failed": (
                    "source_volume_export",
                    "Source volume export failed in MONAI Label",
                ),
                "volume_file_missing": (
                    "source_volume_export",
                    "Source volume export completed but no NIfTI file appeared",
                ),
                "mask_export_failed": (
                    "existing_mask_export",
                    "Existing mask export failed in MONAI Label",
                ),
                "mask_file_missing": (
                    "existing_mask_export",
                    "Existing mask export completed but no NIfTI file appeared",
                ),
                "normalization_failed": (
                    "mask_normalization",
                    "Mask geometry normalization failed",
                ),
                "memory_limit": (
                    "mask_normalization",
                    "Mask preparation exceeded the configured memory limit",
                ),
            }
            failure_stage, failure_message = failure_map.get(
                reason_code,
                ("mask_export", "Mask download or normalization failed"),
            )
            await self._fail_pair(
                item,
                stage=failure_stage,
                message=failure_message,
                details={
                    "reason_code": reason_code,
                    "pair_failure": pair_failure,
                },
                workspace_dir=workspace_dir,
                resolution_outcome=resolution_outcome,
                work_items=work_items,
                run_id=run_id,
                publisher=publisher,
            )
            return False

        # Previews and radiomics both follow item["mask_path"], so this is the
        # single point that decides whether they read the workspace copy or the
        # archived DICOM SEG.
        item["mask_path"] = await self._resolve_mask_path(item, artifact_key)
        item["geometry_adjusted"] = bool(
            (item.get("inference_result") or {}).get("geometry_adjusted")
            or pair_result.get("geometry_adjusted")
        )
        item["geometry"] = pair_result.get("geometry")
        item["alignment_mode"] = pair_result.get("alignment_mode")
        item["legacy_repair_applied"] = bool(
            pair_result.get("legacy_repair_applied", False)
        )
        item["legacy_repair"] = pair_result.get("legacy_repair")
        inference_alignment = item.get("inference_result") or {}
        item["geometry_version"] = (
            inference_alignment.get("geometry_version")
            or pair_result.get("geometry_version")
        )
        item["z_axis_reversed"] = bool(
            inference_alignment.get("z_axis_reversed", False)
        )
        item["x_axis_flipped"] = bool(
            inference_alignment.get("x_axis_flipped", False)
        )
        item["y_axis_flipped"] = bool(
            inference_alignment.get("y_axis_flipped", False)
        )
        item["axis_permutation"] = inference_alignment.get(
            "axis_permutation"
        )
        item["slice_alignment"] = inference_alignment.get("slice_alignment")

        previews = await asyncio.to_thread(
            generate_gif_overlays,
            workspace_dir,
            [item],
        )
        preview = previews[0] if previews else None
        if preview is not None:
            # The overlay stays on disk — it is browser-preview-only,
            # referenced by no report and regenerable from the volume plus its
            # mask — but it is CATALOGUED, so the session's artifacts are
            # enumerable and the preview has an identity that is not a URL.
            await register_render(
                self.session_id or "",
                render_logical_key(
                    str(item.get("pair_id") or ""), str(artifact_key or "")
                ),
                str(preview.get("gifPath") or f"overlays/{artifact_key}.gif"),
                agent_run_id=self.agent_run_id,
                metadata={
                    "schema_version": 1,
                    "pair_id": item.get("pair_id"),
                    "volume_uid": item.get("volume_uid"),
                    "target_id": item.get("target_id") or None,
                    "is_empty_mask": bool(preview.get("isEmptyMask")),
                },
            )
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
            await self._persist_processing_state(
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
        await self._persist_processing_state(
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
            await self._persist_processing_state(
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
            if not item.get("source_segmentation_uid") and not item.get(
                "is_empty_mask"
            ):
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

        await self._persist_processing_state(
            workspace_dir, resolution_outcome, work_items, run_id
        )

        if any(item.get("status") == "ready" for item in ordered):
            await publisher.publish_ready()

        # An interrupted pair keeps its deterministic remote job ID. The
        # pipeline will attach to that job instead of launching another model
        # invocation.
        pending = [
            item
            for item in ordered
            if item.get("status") in {"pending", "inferencing"}
        ]

        async def on_pair_started(event: dict[str, Any]) -> None:
            item = item_by_pair[event["pair_id"]]
            item["status"] = "inferencing"
            await self._persist_processing_state(
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
                is_empty_mask = bool(inference_result.get("is_empty_mask"))
                segmentation_uid = str(
                    inference_result.get("segmentation_series_uid") or ""
                )
                if not segmentation_uid and not is_empty_mask:
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
                        "inference_job_id",
                        "status_code",
                        "geometry_adjusted",
                        "original_mask_shape",
                        "final_mask_shape",
                        "is_empty_mask",
                        "foreground_voxels",
                        "z_axis_reversed",
                        "x_axis_flipped",
                        "y_axis_flipped",
                        "axis_permutation",
                        "slice_alignment",
                        "geometry_version",
                    )
                }
                item["source_segmentation_uid"] = segmentation_uid or None
                item["is_empty_mask"] = is_empty_mask
                item["warning"] = (
                    "No segmentation foreground was found; showing the source "
                    "volume without a mask"
                    if is_empty_mask
                    else None
                )
                item["status"] = "inference_succeeded"
                await self._persist_processing_state(
                    workspace_dir, resolution_outcome, work_items, run_id
                )

                if is_empty_mask:
                    await self._prepare_pair_preview(
                        item,
                        workspace_dir=workspace_dir,
                        resolution_outcome=resolution_outcome,
                        work_items=work_items,
                        run_id=run_id,
                        publisher=publisher,
                    )
                    await self.segmentation_service.purge_inference_job(
                        str(inference_result.get("inference_job_id") or "") or None
                    )
                    return

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

                # The archive row exists now, so the pair can name it (its FK
                # requires a real SEG of the pair's own volume).
                item["candidate_confirmed"] = True
                item["source_method"] = record.get("generation_method") or record.get("method")
                item["source_model"] = (
                    record.get("name") or record.get("model_name") or item["model"]
                )
                item["source_description"] = record.get("series_description") or record.get(
                    "segmentation_type"
                )
                await self._prepare_pair_preview(
                    item,
                    workspace_dir=workspace_dir,
                    resolution_outcome=resolution_outcome,
                    work_items=work_items,
                    run_id=run_id,
                    publisher=publisher,
                )
                await self.segmentation_service.purge_inference_job(
                    str(inference_result.get("inference_job_id") or "") or None
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

    async def _load_checkpoint(
        self,
        workspace_dir: str,
        datasetlist: list[str],
    ) -> tuple[
        TargetResolutionOutcome,
        list[dict[str, Any]],
        str,
        str,
    ] | None:
        """Continue this run's stored batch, if it has one."""
        batch = await self.batch_store.load()
        if batch is None:
            return None
        config = batch.get("config") or {}
        if config.get("segmentation_schema_version") != CURRENT_SCHEMA_VERSION:
            raise BatchReconciliationRequired(
                "The stored segmentation batch was created under schema "
                f"{config.get('segmentation_schema_version')!r} (current "
                f"{CURRENT_SCHEMA_VERSION!r}). It is immutable and cannot be continued "
                "by this code; reconciliation is required."
            )
        if config.get("policy_fingerprint") != CLINICAL_TARGET_POLICY_FINGERPRINT:
            raise BatchReconciliationRequired(
                "The stored segmentation batch was resolved under a different clinical "
                "target policy. Continuing it would apply an old interpretation; "
                "reconciliation is required."
            )
        work_items = work_items_from_batch(batch)
        if not work_items:
            raise BatchReconciliationRequired("The stored segmentation batch has no pairs")
        if {str(item["volume_uid"]) for item in work_items} - set(datasetlist):
            raise BatchReconciliationRequired(
                "The stored segmentation batch names volumes outside the approved cohort"
            )

        correlation_run_id = str(config.get("correlation_run_id") or "")
        workspace = Path(workspace_dir).resolve()
        for fallback_index, item in enumerate(work_items):
            pair_id = str(item["pair_id"])
            item.setdefault("artifact_key", artifact_key_for_pair(pair_id))
            item["sequence_index"] = int(item.get("sequence_index", fallback_index))
            item.setdefault("inference_result", None)
            item["run_id"] = str(item.get("run_id") or correlation_run_id)
            item.setdefault("gif_path", None)
            item.setdefault("reuse_candidate_uid", None)
            item.setdefault("reused", False)
            item.setdefault("is_empty_mask", False)
            if item.get("mask_path"):
                workspace_subpath(workspace, str(item["mask_path"])).relative_to(workspace)
            if item.get("gif_path"):
                workspace_subpath(workspace, str(item["gif_path"])).relative_to(workspace)
            if item.get("status") == "resolved":
                item["status"] = "reusing" if item.get("reused") else "preparing"

        outcome = TargetResolutionOutcome.model_validate(
            {
                "schema_version": config["segmentation_schema_version"],
                "plan": config["final_plan"],
                "intent_plan": config["intent_plan"],
                "candidate_plan": config["candidate_plan"],
                "policy_fingerprint": config["policy_fingerprint"],
            }
        )
        stage = {
            "not_ready": "processing",
            "pending": "awaiting_review",
            "submitting": "awaiting_review",
            "submitted": "reviewed",
        }[batch["review_status"]]
        self.segmentation_run_id = str(batch["segmentation_run_id"])
        return outcome, work_items, correlation_run_id, stage

    @staticmethod
    def _build_result(
        segmentation_run_id: str | None,
        plan,
        work_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        failures = [
            item for item in work_items if item["status"] in {"failed", "rejected"}
        ]
        accepted_count = sum(
            item.get("review_status") in {"accepted", "modified"}
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
            "segmentation_run_id": segmentation_run_id,
            "segmentation_method": (
                f"{plan.chosen_path}: {', '.join(plan.selected_models)}"
            ),
            "targets": plan.target_labels,
            "pair_count": len(work_items),
            "ready_count": sum(item["status"] == "ready" for item in work_items),
            "accepted_count": accepted_count,
            "reused_count": sum(bool(item.get("reused")) for item in work_items),
            "generated_count": sum(
                not item.get("reused")
                and (
                    item.get("source_segmentation_uid") is not None
                    or bool(item.get("is_empty_mask"))
                )
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

    def _apply_submitted_review(
        self,
        workspace_dir: str,
        batch: dict[str, Any],
        work_items: list[dict[str, Any]],
    ) -> None:
        """Bring the in-memory items and the disposable mask files in line with the review PostgreSQL recorded."""
        decisions = {
            pair["pair_key"]: pair.get("review_decision")
            for pair in batch.get("pairs") or []
        }
        accepted = [key for key, value in decisions.items() if value == "accepted"]
        modified = [key for key, value in decisions.items() if value == "modified"]
        cleanup_discarded(
            workspace_dir,
            accepted,
            modified,
            {item["pair_id"]: ensure_artifact_key(item) for item in work_items},
        )
        selected = {
            pair["pair_key"]: pair.get("selected_segmentation_uid")
            for pair in batch.get("pairs") or []
        }
        for item in work_items:
            pair_id = item["pair_id"]
            decision = decisions.get(pair_id)
            artifact_key = ensure_artifact_key(item)
            if decision == "modified":
                temp_path = workspace_subpath(
                    workspace_dir,
                    f"temp_segmentations/seg_{artifact_key}_modified.nii.gz",
                )
                target_path = workspace_subpath(workspace_dir, f"masks/{artifact_key}.nii.gz")
                if temp_path.exists():
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(temp_path, target_path)
                    item["mask_path"] = f"masks/{artifact_key}.nii.gz"
                item["is_empty_mask"] = False
                item["warning"] = None
                item["review_status"] = "modified"
                item["selected_segmentation_uid"] = selected.get(pair_id)
            elif decision == "accepted":
                item["review_status"] = "accepted"
                item["selected_segmentation_uid"] = selected.get(pair_id)
            elif decision == "rejected":
                item["review_status"] = "rejected"
                item["status"] = "rejected"

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
        batch = await self.batch_store.read(self.segmentation_run_id)
        if batch["review_status"] not in {"submitting", "submitted"}:
            await self._write_manifest(
                workspace_dir,
                resolution_outcome,
                work_items,
                stage="awaiting_review",
                run_id=run_id,
            )
        ready_items = [
            item
            for item in work_items
            if item["status"] == "ready" and item.get("review_status", "pending") == "pending"
        ]
        await publisher.publish_final()
        if batch["review_status"] != "submitted" and ready_items:
            await emit_tool_events(
                event_types=["tool_started", "tool_finished"],
                content=TextPayload(
                    message=(
                        f"{len(ready_items)} mask(s) are ready"
                        " — waiting for your review!"
                    )
                ),
                session_id=self.session_id,
            )
            await self._wait_for_review()
            batch = await self.batch_store.read(self.segmentation_run_id)
            if batch["review_status"] != "submitted":
                raise RuntimeError(
                    "The segmentation review wait ended but PostgreSQL has no submitted "
                    "review for this batch."
                )
        if batch["review_status"] == "submitted":
            self._apply_submitted_review(workspace_dir, batch, work_items)
            if not any(
                item.get("review_status") in {"accepted", "modified"} for item in work_items
            ):
                logger.warning("Human review accepted no segmentation masks")

        result = self._build_result(self.segmentation_run_id, plan, work_items)
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

    async def run(self, workspace_dir: str):
        if (
            not self.model_client
            or not self.discovery_tools
            or not self.segmentation_service
        ):
            raise RuntimeError("SegmentationModule.initialize() must be called first")

        self.batch_store = SegmentationBatchStore(self.agent_run_id, client=self.http_client)
        datasetlist, cohort_id, state = await self._load_run_context()
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
        checkpoint = await self._load_checkpoint(workspace_dir, datasetlist)
        if checkpoint is not None:
            (
                resolution_outcome,
                work_items,
                run_id,
                checkpoint_stage,
            ) = checkpoint
            if checkpoint_stage == "reviewed":
                logger.info("Resuming completed segmentation review checkpoint")
                batch = await self.batch_store.read(self.segmentation_run_id)
                self._apply_submitted_review(workspace_dir, batch, work_items)
                return self._build_result(
                    self.segmentation_run_id,
                    resolution_outcome.plan,
                    work_items,
                )
            publisher = self._create_publisher(
                resolution_outcome.plan, work_items, workspace_dir
            )
            if checkpoint_stage == "processing":
                logger.info("Resuming progressive segmentation processing checkpoint")
                await self._execute_work_items(
                    workspace_dir,
                    resolution_outcome,
                    work_items,
                    run_id,
                    publisher,
                )
            else:
                logger.info("Resuming segmentation from awaiting-review checkpoint")
            return await self._complete_review(
                workspace_dir,
                resolution_outcome,
                work_items,
                run_id,
                publisher,
            )

        instructions, locked_roi_interpretation = self._build_instruction(state)
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
        resolution_outcome = await run_planner(
            intent_agent,
            candidate_agent,
            instructions,
            mask_inventory.candidates,
            cache_candidates,
            locked_roi_interpretation=locked_roi_interpretation,
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
                        "idempotency_key": artifact_key_for_pair(
                            f"{run_id}|{pair_id}|{model}|{prompt}"
                        ),
                        "inference_job_id": artifact_key_for_pair(
                            f"{run_id}|{pair_id}|{model}|{prompt}"
                        ),
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
                        # Reused masks come from the archive inventory, so the
                        # pair can name them from the start.
                        "candidate_confirmed": bool(existing),
                        "source_method": (
                            (existing.get("generation_method") or existing.get("method"))
                            if existing
                            else None
                        ),
                        "source_model": (
                            (existing.get("name") or existing.get("model_name"))
                            if existing
                            else None
                        ),
                        "source_description": (
                            (existing.get("series_description") or existing.get("segmentation_type"))
                            if existing
                            else None
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
                        "alignment_mode": None,
                        "legacy_repair_applied": False,
                        "legacy_repair": None,
                        "geometry_version": None,
                        "z_axis_reversed": False,
                        "x_axis_flipped": False,
                        "y_axis_flipped": False,
                        "axis_permutation": None,
                        "slice_alignment": None,
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

        batch = await self.batch_store.create(
            cohort_id=cohort_id,
            config=batch_config(
                resolution_outcome,
                correlation_run_id=run_id,
                schema_version=CURRENT_SCHEMA_VERSION,
            ),
            targets=batch_targets(plan, self._model_for_target),
            work_items=work_items,
        )
        self.segmentation_run_id = str(batch["segmentation_run_id"])
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
