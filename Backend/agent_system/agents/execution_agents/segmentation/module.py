"""Segmentation orchestration with agent-resolved clinical target identity."""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union

import httpx
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools

from agent_system.models.tool_logging import TextPayload
from client_config import create_model_client_from_config

from .logging_config import emit_tool_event, emit_tool_events, setup_logging
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
from .resolution import (
    fetch_cache_candidates,
    fetch_mask_inventory,
    resolve_voxtell_prompt,
    select_newest_exact_target_mask,
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

setup_logging()
logger = logging.getLogger("SegmentationModule")


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

    async def initialize(self):
        self.model_client = create_model_client_from_config()

        current_file = Path(__file__).resolve()
        backend_dir = current_file.parent.parent.parent.parent.parent
        server_path = backend_dir / "mcp" / "server.py"
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
        self.segmentation_service = SegmentationService()

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
        if len(instructions) < 50:
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

    async def _wait_for_new_exact_mask(
        self,
        volume_uid: str,
        canonical_label: str,
        previous_uids: set[str],
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any] | None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            records = await check_having_mask(volume_uid)
            new_records = [
                record
                for record in records
                if str(record.get("label_series_instance_uid") or "")
                not in previous_uids
            ]
            selected = select_newest_exact_target_mask(
                new_records, canonical_label
            )
            if selected:
                return selected
            await asyncio.sleep(5)
        return None

    @staticmethod
    def _write_manifest(
        workspace_dir: str,
        resolution_outcome,
        cache_version: str | None,
        work_items: list[dict[str, Any]],
    ) -> Path:
        plan = resolution_outcome.plan
        manifest_path = Path(workspace_dir) / "segmentation_manifest.json"
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cache_version": cache_version,
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
        manifest_path.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        return manifest_path

    async def _broadcast_review(
        self,
        plan,
        work_items: list[dict[str, Any]],
        workspace_dir: str,
    ) -> list[dict[str, Any]]:
        downloadable = [
            item
            for item in work_items
            if item.get("status") == "ready" and item.get("mask_path")
        ]
        previews = await asyncio.to_thread(
            generate_gif_overlays,
            workspace_dir,
            downloadable,
        )
        preview_by_pair = {entry.get("pairId"): entry for entry in previews}
        for item in downloadable:
            preview = preview_by_pair.get(item["pair_id"])
            if preview and preview.get("isEmptyMask"):
                item["warning"] = "Volume mask is empty"

        frontend_data = {
            "targetRoi": ", ".join(plan.target_labels),
            "targets": [
                {
                    "targetId": target.target_id,
                    "canonicalLabel": target.canonical_label,
                }
                for target in plan.targets
            ],
            "selectedModel": ", ".join(plan.selected_models),
            "volumes": previews,
            "failedVolumes": [
                {
                    "seriesInstanceUid": item["volume_uid"],
                    "pairId": item["pair_id"],
                    "targetId": item["target_id"],
                    "reason": item.get("error") or item.get("warning"),
                }
                for item in work_items
                if item.get("status") == "failed" or item.get("warning")
            ],
            "progress": {
                "total": len(work_items),
                "generated": len(previews),
                "failed": sum(
                    item.get("status") == "failed" for item in work_items
                ),
            },
            "isFinished": True,
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                await client.post(
                    "http://localhost:8150/ws-broadcast",
                    json={
                        "type": "segmentation_output",
                        "payload": frontend_data,
                        "session_id": self.session_id,
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning("Could not broadcast segmentation review: %s", exc)
        return previews

    async def _wait_for_review(self) -> dict[str, list[str]]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    "http://localhost:8150/ws-broadcast",
                    json={
                        "type": "agent_waiting_for_user_input",
                        "payload": {
                            "agent": "segmentation_agent",
                            "status": "WAITING_FOR_REVIEW",
                        },
                        "session_id": self.session_id,
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning("Could not publish review waiting state: %s", exc)

        async with httpx.AsyncClient(timeout=3610.0) as client:
            while True:
                try:
                    response = await client.get(
                        "http://localhost:8150/segmentation-review/wait/"
                        f"{self.session_id}"
                    )
                    if response.status_code == 408:
                        await asyncio.sleep(0.5)
                        continue
                    response.raise_for_status()
                    return response.json()
                except httpx.ReadTimeout:
                    await asyncio.sleep(0.5)

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
        instructions = self._build_instruction(state)

        await emit_tool_events(
            event_types=["tool_started", "tool_progress"],
            content=TextPayload(message="Resolving segmentation targets..."),
            session_id=self.session_id,
        )

        # A failed existing-mask request is fatal by design: silently assuming
        # no masks would create duplicate segmentations.
        mask_inventory = await fetch_mask_inventory(
            datasetlist, check_having_mask
        )
        cache_candidates, cache_version = await fetch_cache_candidates(MONAI_URL)

        # Construct fresh stage agents for every run. This AutoGen version does
        # not expose AssistantAgent.reset(), and fresh instances guarantee that
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

        work_items: list[dict[str, Any]] = []
        for target_index, target in enumerate(plan.targets):
            prompt, cache_hit = resolve_voxtell_prompt(
                target, cache_candidates
            )
            model = self._model_for_target(plan, target_index)
            for volume_uid in datasetlist:
                existing = select_newest_matching_mask(
                    volume_uid, target, mask_inventory
                )
                work_items.append(
                    {
                        "pair_id": f"{volume_uid}__{target.target_id}",
                        "volume_uid": volume_uid,
                        "target_id": target.target_id,
                        "canonical_label": target.canonical_label,
                        "inference_prompt": prompt,
                        "cache_hit": cache_hit,
                        "model": model,
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
                        "status": "resolved" if existing else "pending",
                        "review_status": "pending",
                        "mask_path": None,
                        "error": None,
                    }
                )

        await emit_tool_event(
            event_type="tool_progress",
            content=TextPayload(
                message=(
                    f"Resolved {len(plan.targets)} target(s) across "
                    f"{len(datasetlist)} volume(s)."
                )
            ),
            session_id=self.session_id,
        )

        for target_index, target in enumerate(plan.targets):
            pending = [
                item
                for item in work_items
                if item["target_id"] == target.target_id
                and item["status"] == "pending"
            ]
            if not pending:
                continue
            volumes = [item["volume_uid"] for item in pending]
            failed_uids: set[str] = set()
            model = self._model_for_target(plan, target_index)

            try:
                if plan.chosen_path == "automatic":
                    result = await run_automatic_pipeline(
                        volumes,
                        [target.canonical_label],
                        [model],
                        self.segmentation_service,
                        self.session_id,
                        inference_prompts={
                            target.canonical_label: pending[0]["inference_prompt"]
                        },
                    )
                    failed_uids.update(result.get("failed_uids", []))
                else:
                    _, failures = await run_interactive_pipeline(
                        self.UserFallbackAgent,
                        self.UserProxyAgent,
                        volumes,
                        [target.canonical_label],
                        self.segmentation_service,
                        self.session_id,
                        inference_prompts={
                            target.canonical_label: pending[0]["inference_prompt"]
                        },
                    )
                    failed_uids.update(uid for uid, _ in failures)
            except Exception as exc:
                logger.exception(
                    "Target pipeline failed for %s", target.target_id
                )
                failed_uids.update(volumes)
                for item in pending:
                    item["error"] = str(exc)

            for item in pending:
                if item["volume_uid"] in failed_uids:
                    item["status"] = "failed"
                    item["error"] = item["error"] or "Segmentation inference failed"
                    continue
                previous_uids = {
                    str(record.get("label_series_instance_uid") or "")
                    for record in mask_inventory.records_by_volume.get(
                        item["volume_uid"], []
                    )
                }
                record = await self._wait_for_new_exact_mask(
                    item["volume_uid"],
                    target.canonical_label,
                    previous_uids,
                )
                if not record:
                    item["status"] = "failed"
                    item["error"] = (
                        "Timed out waiting for a new mask with exact "
                        f"target_label={target.canonical_label!r}"
                    )
                    continue
                item["source_segmentation_uid"] = record.get(
                    "label_series_instance_uid"
                )
                item["source_method"] = record.get("method")
                item["source_model"] = (
                    record.get("name")
                    or record.get("model_name")
                    or item["model"]
                )
                item["source_description"] = record.get("segmentation_type")
                item["status"] = "resolved"

        resolved_pairs = [
            {
                "volume_uid": item["volume_uid"],
                "target_id": item["target_id"],
                "label_uid": item["source_segmentation_uid"],
            }
            for item in work_items
            if item["status"] == "resolved" and item["source_segmentation_uid"]
        ]
        successful_pairs, skipped_pairs = await download_and_pair_volumes(
            workspace_dir,
            resolved_pairs,
            task_name=(
                f"{plan.selected_models[0]}_segmentation_task"
                if plan.selected_models
                else "segmentation_task"
            ),
        )
        successful_pair_ids = {
            f"{pair['volume_uid']}__{pair['target_id']}"
            for pair in successful_pairs
        }
        skipped_pair_ids = {
            f"{pair['volume_uid']}__{pair['target_id']}"
            for pair in skipped_pairs
        }
        for item in work_items:
            if item["pair_id"] in successful_pair_ids:
                item["status"] = "ready"
                item["mask_path"] = (
                    f"masks/{item['pair_id']}.nii.gz"
                )
            elif item["pair_id"] in skipped_pair_ids or item["status"] == "resolved":
                item["status"] = "failed"
                item["error"] = item["error"] or "Mask download or normalization failed"

        manifest_path = self._write_manifest(
            workspace_dir, resolution_outcome, cache_version, work_items
        )
        ready_items = [item for item in work_items if item["status"] == "ready"]
        if ready_items:
            await self._broadcast_review(
                plan, work_items, workspace_dir
            )
            raw_review = await self._wait_for_review()
            review = self._normalise_review_keys(
                raw_review, work_items, len(plan.targets)
            )
            ready_pair_ids = {
                item["pair_id"]
                for item in work_items
                if item["status"] == "ready"
            }
            # Resolve conflicting or incomplete responses predictably. Modified
            # wins over accepted, accepted wins over rejected, and an omitted
            # ready pair is rejected rather than left as a dangling manifest
            # entry whose mask cleanup removed.
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
            )
            for item in work_items:
                pair_id = item["pair_id"]
                if pair_id in review["modified"]:
                    temp_path = (
                        Path(workspace_dir)
                        / "temp_segmentations"
                        / f"seg_{pair_id}_modified.nii.gz"
                    )
                    target_path = Path(workspace_dir) / "masks" / f"{pair_id}.nii.gz"
                    if temp_path.exists():
                        shutil.copy2(temp_path, target_path)
                    item["review_status"] = "modified"
                elif pair_id in review["accepted"]:
                    item["review_status"] = "accepted"
                elif pair_id in review["rejected"]:
                    item["review_status"] = "rejected"
                    item["status"] = "rejected"
                elif pair_id in successful_pair_ids:
                    item["review_status"] = "pending"

            if not accepted_or_modified:
                logger.warning("Human review accepted no segmentation masks")
            self._write_manifest(
                workspace_dir,
                resolution_outcome,
                cache_version,
                work_items,
            )

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
        result = {
            "status": status,
            "manifest_path": str(manifest_path),
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
                    "error": item.get("error") or item["status"],
                }
                for item in failures
            ],
        }
        await emit_tool_event(
            event_type="tool_finished",
            content=TextPayload(
                message=(
                    f"Segmentation {status}: {accepted_count}/{len(work_items)} "
                    "volume-target pairs accepted."
                )
            ),
            session_id=self.session_id,
        )
        return result


async def test_module():
    workspace_folder = (
        "C:/Users/ibrahim/Local Folder/GP/CUBE.AI/"
        "Backend/agent_system/workspace"
    )
    agent = SegmentationModule(
        session_id="68dac4a1-ecdb-4145-9f23-874a85d1d6bb"
    )
    await agent.initialize()
    return await agent.run(
        os.path.join(workspace_folder, "config.json"),
        workspace_folder,
    )
