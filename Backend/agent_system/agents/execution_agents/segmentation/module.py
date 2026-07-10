"""
SegmentationModule automates ROI segmentation.

The module receives the workspace config and plan state, chooses the best
segmentation path, prepares volume-mask pairs, streams review overlays, and
applies the human review decision before handing off downstream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

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
from .planner import create_planner_agent, run_planner
from .services import SegmentationService
from .utils import (
    cleanup_discarded,
    download_and_pair_volumes,
    exclude_existing_masks,
    generate_gif_overlays,
    get_model_dice_score,
)
from .ws_client_sender import BASE_URL, send_ws_message_remote

setup_logging()

logger = logging.getLogger("SegmentationModule")

AGENT_ID = "segmentation_agent"
TRIVIAL_INSTRUCTIONS_THRESHOLD = 50
MASK_PERSISTENCE_DELAY_SECONDS = 120
REVIEW_POLL_TIMEOUT_SECONDS = 330.0
GIF_QUEUE_POLL_SECONDS = 5.0


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
        self.PlannerAgent = None
        self.UserProxyAgent = None
        self.UserFallbackAgent = None
        self.segmentation_service: SegmentationService | None = None

    async def initialize(self):
        self.model_client = create_model_client_from_config()
        logger.info("Model client initialized with: %s", self.model_client)

        backend_dir = Path(__file__).resolve().parents[4]
        server_path = backend_dir / "mcp" / "server.py"
        logger.info("Starting MCP server at %s with session_id=%s", server_path, self.session_id)

        env = os.environ.copy()
        env.update({"SESSION_ID": self.session_id or "", "AGENT_ID": AGENT_ID})
        mcp_server = StdioServerParams(
            command=sys.executable,
            args=[str(server_path)],
            read_timeout_seconds=2000,
            env=env,
        )

        try:
            tools = await mcp_server_tools(mcp_server)
        except Exception:
            logger.exception("Failed to connect to MCP server at %s", server_path)
            raise

        discovery_tools = [tool for tool in tools if tool.name == "list_models"]
        if not discovery_tools:
            raise RuntimeError("Missing required MCP tool: list_models")

        self.PlannerAgent = create_planner_agent(self.model_client, discovery_tools)
        self.UserFallbackAgent = create_interactive_agent(self.model_client, discovery_tools)
        self.UserProxyAgent = create_user_proxy_agent(self.model_client)
        self.segmentation_service = SegmentationService()
        logger.info("Segmentation agents and service initialized.")

    async def run(self, input_data: str | dict[str, Any], workspace_dir: str):
        config, state = self._load_config_and_state(input_data, workspace_dir)
        instructions = self._get_segmentation_instructions(state)
        datasetlist = config.get("datasetlist", [])
        logger.info("Will prepare segmentations for %s volumes.", len(datasetlist))

        plan = await run_planner(self.PlannerAgent, instructions)
        logger.info("Planner plan: %s", plan)

        target_labels = plan.get("target_labels") or []
        target_label = self._primary_target_label(target_labels)
        chosen_path = plan.get("chosen_path", "interactive")
        selected_model = self._selected_model(plan)

        eligible_volumes = await exclude_existing_masks(
            datasetlist,
            target_labels,
            model_name=selected_model,
        )
        await self._emit_existing_mask_summary(datasetlist, eligible_volumes)

        logger.info("Eligible volumes for segmentation: %s", len(eligible_volumes))
        if eligible_volumes:
            await self._run_segmentation_path(
                chosen_path=chosen_path,
                eligible_volumes=eligible_volumes,
                target_labels=target_labels,
                selected_models=plan.get("selected_models") or [],
            )
        else:
            logger.info("All requested volumes already have matching masks.")

        await emit_tool_event(
            event_type="tool_finished",
            content=TextPayload(message="Segmentation complete."),
            session_id=self.session_id,
        )

        downloaded_uids, skipped_uids = await self._prepare_review_dataset(
            workspace_dir=workspace_dir,
            datasetlist=datasetlist,
            eligible_volumes=eligible_volumes,
            chosen_path=chosen_path,
            selected_model=selected_model,
            target_label=target_label,
        )
        self._append_skip_warning(workspace_dir, skipped_uids)

        gif_volumes = await self._generate_and_stream_overlays(
            workspace_dir=workspace_dir,
            downloaded_uids=downloaded_uids,
            target_label=target_label,
            selected_model=selected_model,
        )
        await self._send_final_frontend_update(
            workspace_dir=workspace_dir,
            target_label=target_label,
            selected_model=selected_model,
            gif_volumes=gif_volumes,
        )

        review_result = await self._wait_for_review()
        await self._apply_review_result(workspace_dir, review_result)
        return {"status": "finished"}

    def _load_config_and_state(
        self,
        input_data: str | dict[str, Any],
        workspace_dir: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if isinstance(input_data, dict):
            if "plan" in input_data:
                return self._read_workspace_config(workspace_dir), input_data
            return input_data, self._read_workspace_state(workspace_dir)

        config_path = Path(input_data)
        state_path = config_path.with_name("state.json")
        with config_path.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
        with state_path.open("r", encoding="utf-8") as state_file:
            state = json.load(state_file)
        return config, state

    def _read_workspace_config(self, workspace_dir: str) -> dict[str, Any]:
        config_path = Path(workspace_dir) / "config.json"
        with config_path.open("r", encoding="utf-8") as config_file:
            return json.load(config_file)

    def _read_workspace_state(self, workspace_dir: str) -> dict[str, Any]:
        state_path = Path(workspace_dir) / "state.json"
        with state_path.open("r", encoding="utf-8") as state_file:
            return json.load(state_file)

    def _get_segmentation_instructions(self, state: dict[str, Any]) -> str:
        steps = state.get("plan", {}).get("steps", [])
        segmentation_step = next((step for step in steps if step.get("module") == AGENT_ID), None)
        if not segmentation_step:
            raise ValueError("Segmentation step not found in state plan.")

        instructions = segmentation_step.get("task_message", {}).get("instructions", "")
        if len(instructions.strip()) >= TRIVIAL_INSTRUCTIONS_THRESHOLD:
            logger.info("Received instructions: %s", instructions)
            return instructions

        hypothesis_title = state.get("hypothesis", {}).get("title", "")
        logger.warning(
            "Instructions are too short (%s chars): '%s'. Falling back to hypothesis title.",
            len(instructions.strip()),
            instructions.strip(),
        )
        return (
            f"Perform ROI segmentation for the study: '{hypothesis_title}'. "
            "Identify the correct anatomical target from the hypothesis title and run the appropriate model."
        )

    async def _emit_existing_mask_summary(self, datasetlist, eligible_volumes) -> None:
        payload = TextPayload(
            message=f"Found existing masks for {len(datasetlist) - len(eligible_volumes)} volumes."
        )
        await emit_tool_events(
            event_types=["tool_started", "tool_finished"],
            content=payload,
            session_id=self.session_id,
        )

    async def _run_segmentation_path(
        self,
        *,
        chosen_path: str,
        eligible_volumes: list[str],
        target_labels: list[str],
        selected_models: list[str],
    ) -> None:
        if self.segmentation_service is None:
            raise RuntimeError("Segmentation service has not been initialized.")

        if chosen_path == "automatic" and selected_models:
            await run_automatic_pipeline(
                eligible_volumes,
                target_labels,
                selected_models,
                self.segmentation_service,
                self.session_id,
            )
            return

        if chosen_path == "automatic":
            logger.warning(
                "Planner selected automatic segmentation without a model. Falling back to interactive path."
            )

        await run_interactive_pipeline(
            self.UserFallbackAgent,
            self.UserProxyAgent,
            eligible_volumes,
            target_labels,
            self.segmentation_service,
            self.session_id,
        )

    async def _prepare_review_dataset(
        self,
        *,
        workspace_dir: str,
        datasetlist: list[str],
        eligible_volumes: list[str],
        chosen_path: str,
        selected_model: str | None,
        target_label: str | None,
    ) -> tuple[list[str], list[str]]:
        payload = TextPayload(message="Creating segmentation review dataset.")
        await emit_tool_events(
            event_types=["tool_started", "tool_progress"],
            content=payload,
            session_id=self.session_id,
        )

        if eligible_volumes:
            logger.info("Waiting %s seconds for MONAI to persist new masks.", MASK_PERSISTENCE_DELAY_SECONDS)
            await asyncio.sleep(MASK_PERSISTENCE_DELAY_SECONDS)

        volumes_to_download = eligible_volumes or datasetlist
        result = await download_and_pair_volumes(
            workspace_dir,
            volumes_to_download,
            chosen_path=chosen_path,
            selected_auto_model_for_volume=selected_model,
            target_label=target_label,
        )
        if isinstance(result, tuple):
            return result
        if isinstance(result, list):
            return result, []
        return [], []

    def _append_skip_warning(self, workspace_dir: str, skipped_uids: list[str]) -> None:
        if not skipped_uids:
            return

        skip_msg = (
            f"WARNING: Skipped {len(skipped_uids)} volumes because they exceeded the "
            f"16 GB RAM decompression limit. UIDs: {', '.join(skipped_uids)}"
        )
        logger.warning(skip_msg)

        scratchpad_path = Path(workspace_dir) / "scratchpad.json"
        if not scratchpad_path.exists():
            return

        try:
            with scratchpad_path.open("r", encoding="utf-8") as scratchpad_file:
                scratchpad = json.load(scratchpad_file)

            agent_section = scratchpad.get("agents", {}).get(AGENT_ID) or scratchpad.get(AGENT_ID)
            if agent_section is None:
                return

            from datetime import datetime, timezone

            agent_section.setdefault("notes", []).append(
                {"content": skip_msg, "created_at": datetime.now(timezone.utc).isoformat()}
            )
            with scratchpad_path.open("w", encoding="utf-8") as scratchpad_file:
                json.dump(scratchpad, scratchpad_file, indent=2)
        except Exception:
            logger.exception("Failed to write skipped-volume warning to scratchpad.")

    async def _generate_and_stream_overlays(
        self,
        *,
        workspace_dir: str,
        downloaded_uids: list[str],
        target_label: str | None,
        selected_model: str | None,
    ) -> list[dict[str, Any]]:
        if not downloaded_uids:
            return []

        gif_queue: asyncio.Queue = asyncio.Queue()
        gif_volumes: list[dict[str, Any]] = []
        dice_score = get_model_dice_score(selected_model, target_label) if selected_model and target_label else None

        def on_gif_ready(volume_entry: dict[str, Any]) -> None:
            gif_queue.put_nowait(volume_entry)

        async def broadcast_progressively(generation_task: asyncio.Task) -> None:
            while True:
                if generation_task.done() and gif_queue.empty():
                    break

                try:
                    volume_entry = await asyncio.wait_for(
                        gif_queue.get(),
                        timeout=GIF_QUEUE_POLL_SECONDS,
                    )
                except asyncio.TimeoutError:
                    if generation_task.done():
                        break
                    logger.debug("Waiting for segmentation GIF generation.")
                    continue

                gif_volumes.append(volume_entry)
                await self._send_frontend_update(
                    target_label=target_label,
                    selected_model=selected_model,
                    dice_score=dice_score,
                    volumes=gif_volumes,
                    latex=None,
                    is_finished=False,
                )
                logger.info("Sent %s/%s segmentation GIFs.", len(gif_volumes), len(downloaded_uids))

        generation_task = asyncio.create_task(
            asyncio.to_thread(generate_gif_overlays, workspace_dir, downloaded_uids, on_gif_ready)
        )
        await asyncio.gather(generation_task, broadcast_progressively(generation_task))
        return gif_volumes

    async def _send_final_frontend_update(
        self,
        *,
        workspace_dir: str,
        target_label: str | None,
        selected_model: str | None,
        gif_volumes: list[dict[str, Any]],
    ) -> None:
        dice_score = get_model_dice_score(selected_model, target_label) if selected_model and target_label else None
        await self._send_frontend_update(
            target_label=target_label,
            selected_model=selected_model,
            dice_score=dice_score,
            volumes=gif_volumes,
            latex=self._read_initial_latex(workspace_dir),
            is_finished=True,
        )

    async def _send_frontend_update(
        self,
        *,
        target_label: str | None,
        selected_model: str | None,
        dice_score: float | None,
        volumes: list[dict[str, Any]],
        latex: str | None,
        is_finished: bool,
    ) -> None:
        payload = {
            "targetRoi": target_label,
            "selectedModel": selected_model,
            "diceScore": dice_score,
            "volumes": volumes,
            "latex": latex,
            "isFinished": is_finished,
        }
        try:
            await send_ws_message_remote("segmentation_output", payload, session_id=self.session_id)
        except Exception:
            logger.exception("Failed to send segmentation_output update.")

    def _read_initial_latex(self, workspace_dir: str) -> str | None:
        tex_path = Path(workspace_dir) / "initial_report.tex"
        try:
            return tex_path.read_text(encoding="utf-8") if tex_path.exists() else None
        except Exception:
            logger.exception("Failed to read initial_report.tex.")
            return None

    async def _wait_for_review(self) -> dict[str, list[str]]:
        logger.info("Waiting for human segmentation review via HTTP long-poll.")
        review_url = f"{BASE_URL}/segmentation-review/wait/{self.session_id}"
        async with httpx.AsyncClient(timeout=REVIEW_POLL_TIMEOUT_SECONDS) as client:
            while True:
                try:
                    response = await client.get(review_url)
                    if response.status_code == 408:
                        logger.info("Review long-poll timed out on server; reconnecting.")
                        continue

                    response.raise_for_status()
                    return response.json()
                except httpx.ReadTimeout:
                    logger.info("Review client read timeout reached; reconnecting.")

    async def _apply_review_result(self, workspace_dir: str, review_result: dict[str, list[str]]) -> None:
        accepted = review_result.get("accepted", [])
        rejected = review_result.get("rejected", [])
        modified = review_result.get("modified", [])
        logger.info("Review complete. Accepted=%s Rejected=%s Modified=%s", accepted, rejected, modified)

        cleanup_discarded(workspace_dir, accepted, modified)
        if modified:
            logger.info("Fetching updated labels for modified UIDs.")
            await download_and_pair_volumes(
                workspace_dir,
                datasetlist=modified,
                chosen_path="interactive",
                selected_auto_model_for_volume=None,
                download_volumes=False,
            )

    def _selected_model(self, plan: dict[str, Any]) -> str | None:
        selected_models = plan.get("selected_models") or []
        return selected_models[0] if selected_models else None

    def _primary_target_label(self, target_labels: list[str]) -> str | None:
        return target_labels[0] if target_labels else None

__all__ = ["SegmentationModule"]
