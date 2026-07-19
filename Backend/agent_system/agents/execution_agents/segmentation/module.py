"""
SegmentationModule – automates ROI segmentation.
The module receives an imaging protocol and raw volumes, then runs a
segmentation model (e.g., nnUNet, MONAI) to produce binary masks for the
structure
"""
import asyncio
import json
import logging
import os
import shutil
import httpx
from pathlib import Path
from typing import Any, Dict, List, Union
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools
from client_config import create_model_client_from_config
from agent_system.models.tool_logging import TextPayload
from .planner import create_planner_agent, run_planner
from .pipelines.automatic import run_automatic_pipeline
from .pipelines.interactive import run_interactive_pipeline, create_interactive_agent, create_user_proxy_agent
from .services import SegmentationService
from .utils import exclude_existing_masks, download_and_pair_volumes, generate_gif_overlays, cleanup_discarded
from .logging_config import setup_logging, emit_tool_event, emit_tool_events
setup_logging()

logger = logging.getLogger("SegmentationModule")

class SegmentationModule:

    def __init__(self, config_path: str = "config.json", model_name: str = "gpt-oss:120b-cloud", session_id: str = None):

        self.config_path = config_path
        self.model_name = model_name
        self.session_id = session_id
        self.model_client = None  
        self.PlannerAgent = None
        self.UserProxyAgent = None
        self.UserFallbackAgent = None
        self.segmentation_service = None


    async def initialize(self):
        # intialize model client
        self.model_client = create_model_client_from_config()
        logger.info(f"Model client initialized with : {self.model_client}")

        # MCP server connection
        current_file = Path(__file__).resolve()
        backend_dir = current_file.parent.parent.parent.parent.parent
        server_path = backend_dir / "mcp" / "server.py"   
        logger.info(f"Starting MCP server at: {server_path}, with session id: {self.session_id}")
        env = os.environ.copy()
        env.update({
            "SESSION_ID": self.session_id, "AGENT_ID": 'segmentation_agent'
        })
        math_server = StdioServerParams(
            command="python", args=[str(server_path)], read_timeout_seconds=2000, env=env
        )
        try:
            tools = await mcp_server_tools(math_server)
            logger.info(f"Connected! Available tools count: {len(tools)}")
            
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            raise 
        
        self.discovery_tools = [t for t in tools if t.name == "list_models"]
        assert self.discovery_tools, "Missing list_models" 
        
        # intialize agents and services
        self.PlannerAgent = create_planner_agent(self.model_client, self.discovery_tools)
        logger.info("PlannerAgent initialized.")
        self.UserFallbackAgent = create_interactive_agent(self.model_client, self.discovery_tools)
        self.UserProxyAgent = create_user_proxy_agent(self.model_client)
        self.segmentation_service = SegmentationService()


    async def run(self, input_data: Union[str, Dict[str, Any]], workspace_dir: str):
        # input data is config_path
        if isinstance(input_data, str):
            state_path = input_data.replace('config', 'state')
            with open(state_path, 'r') as f:
                state = json.load(f)
            with open(input_data, "r") as f:
                input_data = json.load(f)
        steps = state.get("plan", {}).get("steps", [])

        segmentation_step = next(
            (s for s in steps if s.get("module") == "segmentation_agent"),
            None
        )

        if not segmentation_step:
            raise ValueError("Segmentation step not found")

        instructions = segmentation_step["task_message"]["instructions"]
        # Guard: if planning stored only a trivial stub (e.g. "fix", "run"),
        # fall back to the hypothesis title so the PlannerAgent can still
        # infer the correct anatomical target and model.
        TRIVIAL_THRESHOLD = 50
        if len(instructions.strip()) < TRIVIAL_THRESHOLD:
            hypothesis_title = state.get("hypothesis", {}).get("title", "")
            logger.warning(
                f"Instructions appear trivially short ({len(instructions.strip())} chars): "
                f"'{instructions.strip()}'. Falling back to hypothesis title as instruction context."
            )
            instructions = (
                f"Perform ROI segmentation for the study: '{hypothesis_title}'. "
                f"Identify the correct anatomical target from the hypothesis title and run the appropriate model."
            )

        # ── Always enrich instructions with full hypothesis context ────────
        # The Pipeline Architect's instructions may say "segment the spleen"
        # when the hypothesis actually says "cancerous spleens".  By appending
        # the hypothesis statement, population, and clinical context, the
        # PlannerAgent can distinguish healthy-organ vs pathology targets.
        hyp_data = state.get("hypothesis", {})
        hyp_enrichment_parts = []
        if hyp_data.get("hypothesis"):
            hyp_enrichment_parts.append(f"Hypothesis: {hyp_data['hypothesis']}")
        if hyp_data.get("population"):
            pop = hyp_data["population"]
            if isinstance(pop, dict):
                pop = ", ".join(f"{k}: {v}" for k, v in pop.items())
            hyp_enrichment_parts.append(f"Population: {pop}")
        if hyp_data.get("clinical_context"):
            hyp_enrichment_parts.append(f"Clinical context: {hyp_data['clinical_context']}")

        if hyp_enrichment_parts:
            enrichment = "\n".join(hyp_enrichment_parts)
            instructions = (
                f"{instructions}\n\n"
                f"── HYPOTHESIS CONTEXT (use this to determine the correct segmentation target) ──\n"
                f"{enrichment}\n\n"
                f"IMPORTANT: If the hypothesis mentions disease/pathology (cancer, tumor, lesion, "
                f"malignancy), the segmentation target must be the PATHOLOGY on the organ, "
                f"NOT the healthy organ alone."
            )

        logger.info(f"Received instructions: {instructions}")
        datasetlist: List[str] = input_data.get("datasetlist", [])
        logger.info(f"Will prepare segmentations for {len(datasetlist)} volumes.")
        #1. plan
        plan = await run_planner(self.PlannerAgent,instructions)
        logger.info(f"Planner plan: {plan}")
        target_labels = plan["target_labels"]
        chosen_path = plan["chosen_path"]
        # Pass the selected model so a volume is only excluded when it already
        # has a mask from the SAME model+organ pair.  A liver-voxtell mask will
        # NOT block a fresh vista liver segmentation.
        selected_models = plan.get("selected_models") or []
        model_for_exclusion = selected_models[0] if selected_models else None
        eligible_volumes = await exclude_existing_masks(
            datasetlist, target_labels, model_name=model_for_exclusion
        )
        # ====== Logging before Segmentation =================
        await emit_tool_events(
            event_types=["tool_started", "tool_finished"],
            content=TextPayload(
                message=f'Found existing masks for {len(datasetlist) - len(eligible_volumes)} volumes'
            ),
            session_id=self.session_id,
        )
        
        # =========== test ===============

        # plan ={'target_labels': ['Spleen'], 'chosen_path': 'automatic','selected_models': ['segmentation_spleen']}
        # =================================
        
        logger.info(
            f"Eligible volumes for segmentation: {len(eligible_volumes)}"
        )
        # ---- Run segmentation (automatic or interactive) ----
        pipeline_failed_uids = []  # UIDs that couldn't be segmented (e.g. OOM kill)
        skip_wait = False

        if chosen_path == "automatic" and eligible_volumes:
            selected_auto_models = plan["selected_models"] if plan["selected_models"] else None
            try:
                seg_result = await run_automatic_pipeline(
                    eligible_volumes, target_labels, selected_auto_models,
                    self.segmentation_service, self.session_id
                )
                # run_automatic_pipeline now returns a dict on partial failure
                if isinstance(seg_result, dict) and seg_result.get("partial_failure"):
                    pipeline_failed_uids = seg_result.get("failed_uids", [])
                    logger.warning(
                        "Partial segmentation failure: %d volumes could not be processed: %s",
                        len(pipeline_failed_uids), pipeline_failed_uids
                    )
            except Exception as seg_exc:
                # Defensive catch — any unexpected error should not abort the
                # download / GIF / human-review flow for the volumes that DID succeed.
                logger.error(
                    "run_automatic_pipeline raised an unexpected error: %s — "
                    "proceeding to download/review for remaining volumes.",
                    seg_exc, exc_info=True
                )

        elif eligible_volumes:
            selected_interactive_model = await run_interactive_pipeline(
                self.UserFallbackAgent, self.UserProxyAgent,
                eligible_volumes, target_labels, self.segmentation_service, self.session_id
            )
            if not selected_interactive_model:
                skip_wait = True

        # ========= logging after Segmentation ================
        await emit_tool_event(
            event_type="tool_finished",
            content=TextPayload(message="Segmentation...Done"),
            session_id=self.session_id,
        )

        # ===========  logging before download ====================
        await emit_tool_events(
            event_types=["tool_started", "tool_progress"],
            content=TextPayload(message="Creating dataset..."),
            session_id=self.session_id,
        )
        # =========== download ========================    
        # Give MONAI a moment to persist new masks — but only when segmentation
        # actually ran.  If all volumes already had existing masks (eligible_volumes
        # is empty), there is nothing to wait for.
        if eligible_volumes and not skip_wait:
            logger.info("Waiting 120 s for MONAI to persist new segmentation masks...")
            await asyncio.sleep(120)
        elif skip_wait:
            logger.info("Skipping 120s wait because no AI segmentation was performed (manual mode fallback).")

        # Exclude volumes that failed segmentation — only download successful ones
        successful_eligible = [v for v in eligible_volumes if v not in pipeline_failed_uids]
        # Volumes that already had a valid mask (excluded from segmentation) must still
        # go through download/GIF/QC so they appear alongside newly segmented volumes.
        already_masked = [v for v in datasetlist if v not in eligible_volumes]
        volumes_to_download = successful_eligible + already_masked if (successful_eligible or already_masked) else (
            datasetlist if not eligible_volumes else []
        )
        plan_model = plan["selected_models"][0] if plan.get("selected_models") else None
        plan_target = target_labels[0] if target_labels else None
        result = await download_and_pair_volumes(
            workspace_dir,
            volumes_to_download,
            chosen_path=chosen_path,
            selected_auto_model_for_volume=plan_model,
            target_label=plan_target,
        )
        # download_and_pair_volumes returns (successful_list, skipped_list) normally,
        # but returns [] if datasetlist was empty — guard against both.
        if isinstance(result, tuple):
            downloaded_uids, skipped_uids = result
        else:
            downloaded_uids, skipped_uids = [], []
        
        # --- Log skipped volumes to scratchpad ---
        if skipped_uids:
            skip_msg = f"WARNING: Skipped {len(skipped_uids)} volumes because they exceeded the 16 GB RAM decompression limit. UIDs: {', '.join(skipped_uids)}"
            logger.warning(skip_msg)
            
            # Append to scratchpad.json directly
            scratchpad_path = os.path.join(workspace_dir, "scratchpad.json")
            if os.path.exists(scratchpad_path):
                try:
                    with open(scratchpad_path, 'r', encoding='utf-8') as f:
                        sp_data = json.load(f)
                    
                    if "segmentation_agent" in sp_data:
                        from datetime import datetime, timezone
                        sp_data["segmentation_agent"].setdefault("notes", []).append({
                            "content": skip_msg,
                            "created_at": datetime.now(timezone.utc).isoformat()
                        })
                        with open(scratchpad_path, 'w', encoding='utf-8') as f:
                            json.dump(sp_data, f, indent=2)
                except Exception as e:
                    logger.error(f"Failed to write to scratchpad: {e}")
        # -----------------------------------------
        
        #============================ test segmentation view ===================================
        # downloaded_uids = ["1.2.826.0.1.3680043.8.498.15136386566608168882487015520907740870"]
        # ======================================================================================
        
        # ================== Generate Overlays (progressive) ====================
        gif_volumes = []
        if downloaded_uids:
            # Use a queue to bridge the sync GIF thread → async broadcaster
            gif_queue: asyncio.Queue = asyncio.Queue()
            def _on_gif_ready(volume_entry: dict):
                """Called synchronously from the GIF thread; puts the entry into the queue."""
                gif_queue.put_nowait(volume_entry)

            async def _broadcast_progressively():
                """Reads from the queue and broadcasts each GIF as it arrives."""
                pending = len(downloaded_uids)
                while pending > 0:
                    try:
                        volume_entry = await asyncio.wait_for(gif_queue.get(), timeout=300.0)
                    except asyncio.TimeoutError:
                        logger.warning("GIF progressive broadcast: timed out waiting for next GIF.")
                        break
                    gif_volumes.append(volume_entry)
                    pending -= 1
                    try:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            await client.post(
                                "http://localhost:8150/ws-broadcast",
                                json={
                                    "type": "segmentation_output",
                                    "payload": {
                                        "targetRoi": plan_target,
                                        "selectedModel": plan_model,
                                        "diceScore": None,
                                        "volumes": gif_volumes,   # cumulative list
                                        "latex": None,
                                        "isFinished": False,
                                    },
                                    "session_id": self.session_id
                                }
                            )
                        logger.info(f"Progressive broadcast: sent {len(gif_volumes)}/{len(downloaded_uids)} GIFs")
                    except Exception as broadcast_err:
                        logger.warning(f"Progressive broadcast failed for {volume_entry.get('seriesInstanceUid')}: {broadcast_err}")

            # Run GIF generation (blocking) and progressive broadcaster concurrently
            await asyncio.gather(
                asyncio.to_thread(generate_gif_overlays, workspace_dir, downloaded_uids, _on_gif_ready),
                _broadcast_progressively(),
            )
            logger.info(f"gif_volumes sample: {gif_volumes[:1]}")



        #=========== Prepare final summary payload for frontend ========================
        # GIFs were already sent progressively — this final message attaches latex
        # and confirms all volumes are done. Volumes list kept for completeness.
        latex_content = None
        try:
            tex_path = Path(workspace_dir) / "initial_report.tex"
            if tex_path.exists():
                latex_content = tex_path.read_text(encoding="utf-8")
        except Exception:
            latex_content = None

        # Always send final summary to signal completion!
        # plan_model / plan_target were set when download_and_pair_volumes was called above
        frontend_data = {
            "targetRoi": plan_target,
            "selectedModel": plan_model,
            "diceScore": None,
            "volumes": gif_volumes,
            "latex": latex_content,
            "isFinished": True
        }
        logger.info("Module sending final summary to frontend...")
        async with httpx.AsyncClient(timeout=60.0) as client:
            await client.post(
                "http://localhost:8150/ws-broadcast",
                json={
                    "type": "segmentation_output",
                    "payload": frontend_data,
                    "session_id": self.session_id
                }
            )


        # Wait for user review message
        logger.info("Module paused: Waiting for human review via HTTP long-poll...")
        async with httpx.AsyncClient(timeout=330.0) as client:
                    while True:
                        try:
                            response = await client.get(
                                f"http://localhost:8150/segmentation-review/wait/{self.session_id}"
                            )

                            # if time out reconnect
                            if response.status_code == 408:
                                logger.info("Server ended the long-poll (408 Timeout). Reconnecting...")
                                continue
                            # for other errors, log and raise
                            response.raise_for_status()
                            # successful response, break the loop and process results
                            user_results = response.json()
                            break
                        except httpx.ReadTimeout:
                            logger.info("Client read timeout reached. Reconnecting...")
                            continue

        final_accepted_uids = user_results.get("accepted", [])
        final_rejected_uids = user_results.get("rejected", [])
        final_modified_uids = user_results.get("modified", [])
        logger.info(f"Module resumed! Accepted: {final_accepted_uids}, Rejected: {final_rejected_uids}, Modified: {final_modified_uids}")

        # #============= accepted and modified uids just kept only in the folder=================
        cleanup_discarded(workspace_dir, final_accepted_uids, final_modified_uids)
        if final_modified_uids:
            logger.info("Fetching updated labels for modified UIDs...")
            for uid in final_modified_uids:
                temp_mask_path = os.path.join(workspace_dir, "temp_segmentations", f"seg_{uid}_modified.nii.gz")
                target_mask_path = os.path.join(workspace_dir, "masks", f"{uid}.nii.gz")
                if os.path.exists(temp_mask_path):
                    logger.info(f"Using explicitly uploaded modified mask for {uid}")
                    shutil.copy2(temp_mask_path, target_mask_path)
                else:
                    logger.info(f"No explicitly uploaded mask found for {uid}, falling back to datastore")
                    await download_and_pair_volumes(
                        workspace_dir,
                        datasetlist=[uid],
                        chosen_path="interactive",
                        selected_auto_model_for_volume=plan_model,
                        target_label=plan_target,
                        download_volumes=False
                    )

            # --- REGENERATE GIFS FOR MODIFIED MASKS ---
            logger.info("Re-generating GIFs for modified masks...")
            modified_gif_entries = []
            def _on_gif_ready_modified(volume_entry: dict):
                modified_gif_entries.append(volume_entry)
            
            await asyncio.to_thread(generate_gif_overlays, workspace_dir, final_modified_uids, _on_gif_ready_modified)
            
            # Update gif_volumes array with the new entries
            for mod_entry in modified_gif_entries:
                for i, existing_entry in enumerate(gif_volumes):
                    if existing_entry.get("seriesInstanceUid") == mod_entry.get("seriesInstanceUid"):
                        gif_volumes[i] = mod_entry
                        break
            
            # Re-broadcast final summary so the chat UI updates the GIFs!
            frontend_data["volumes"] = gif_volumes
            logger.info("Module sending updated summary to frontend with modified GIFs...")
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    await client.post(
                        "http://localhost:8150/ws-broadcast",
                        json={
                            "type": "segmentation_output",
                            "payload": frontend_data,
                            "session_id": self.session_id
                        }
                    )
            except Exception as e:
                logger.error(f"Failed to rebroadcast updated GIFs: {e}")

        return {"status": "finished"}
    
# for testing the agent alone 
async def test_module():
    """Test function for the SegmentationModule"""
    try:
        logger.info("Starting SegmentationModule test...")
        
        agent = SegmentationModule(session_id="68dac4a1-ecdb-4145-9f23-874a85d1d6bb")
        await agent.initialize()
        
        current_file = Path(__file__)
        workspace_folder = os.path.join(current_file.parent.parent.parent.parent,"workspace_68dac4a1-ecdb-4145-9f23-874a85d1d6bb")
        config_path = os.path.join(workspace_folder, "config.json")
        result = await agent.run(config_path, workspace_folder)
        
        logger.info("Test completed successfully!")
        logger.info(f"Result: {result}")
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(test_module())

__all__ = ["SegmentationModule"]
