import sys
import os
import json
import logging
from datetime import datetime
import re
import asyncio
import aiofiles
from pathlib import Path
from typing import Callable, Awaitable, Any

from autogen_agentchat.messages import TextMessage

from services.session_service import SessionService
# from .models import PlannerState, ClinicalHypothesis, StudyPlan, StudyStep
from .planning_agents import create_planner_agents, create_model_client
from .db_state_store import PlannerDbStateStore
from models.plans import *
from models.session import Session
from .context_compiler import compile_task_messages, build_enriched_pipeline_prompt, summarize_knowledge_for_planning, _build_hypothesis_summary
from agents.literature_review_agent.knowledge_builder import build_knowledge_base
from agents.literature_review_agent.literature_review_agent import run_literature_review

# Type aliases
InputProvider = Callable[[], Awaitable[str]]
OutputCallback = Callable[[str, Any], Awaitable[None]]

logger = logging.getLogger(__name__)


class ClinicalPlanner:
    """
    Three-phase clinical research planner:
      Phase 1 — Hypothesis Structuring (PICO format)
      Phase 2 — Literature Review & Knowledge Building
      Phase 3 — Dynamic Pipeline Planning (knowledge-informed)
    """

    def __init__(
        self, 
        input_provider: InputProvider,
        output_callback: OutputCallback,
        workspace_dir: str,
        api_key: str,
        session: Session,
        session_service: SessionService,
        model_client=None,
    ):
        self.input_provider = input_provider
        self.output_handler = output_callback
        root_dir = Path(__file__).parent.parent.parent
        log_dir = os.path.join(root_dir, "logs")
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = os.path.join(log_dir, f"planner_{timestamp}.log")
        self.log_path = Path(log_file)
        

        self.state_path = Path(workspace_dir) / "state.json"

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        
        
        if model_client is not None:
            self.client = model_client
        else:
            self.client = create_model_client(api_key or "")
        self.agents = create_planner_agents(self.client, input_func=self._user_proxy_input_adapter)
        
        self.state = PlannerState()
        self.db_state_store = PlannerDbStateStore()
        self.session = session
        self.session_service = session_service
        self._initialize_logging()

    async def _user_proxy_input_adapter(self, prompt: str = None, cancellation_token=None) -> str:
        return await self.input_provider()

    def _initialize_logging(self):
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        file_handler = logging.FileHandler(self.log_path)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(message)s'))
        std_handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(file_handler)
        logger.addHandler(std_handler)

    async def initialize(self):
        await self._load_state()

    async def _send(self, type_: str, data: Any, session_id: str = None) -> None:
        await self.output_handler(type_, data, self.session.session_id)

    def _clean_json(self, text: str) -> str:
        """Extract JSON from LLM response, handling markdown code fences anywhere."""
        text = text.strip()
        text = text.replace("APPROVE", "")

        fence_match = re.search(r"```json\s*(\{.*\})\s*```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()

        fence_match = re.search(r"```\s*(\{.*\})\s*```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()

        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```", "", text)
        text = re.sub(r"```$", "", text)

        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            return brace_match.group(0).strip()

        return text.strip()

    @staticmethod
    def _fix_invalid_json_escapes(text: str) -> str:
        """Fix invalid backslash escapes that break json.loads().

        LLMs often embed LaTeX (\\documentclass, \\begin{table}) or Windows
        paths (\\Users) in JSON string values.  These are not valid JSON
        escape sequences and cause ``Invalid \\escape`` errors.

        Valid JSON escapes are: \\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX
        Any backslash NOT followed by one of those characters is doubled so
        json.loads() will accept it.
        """
        return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)

    # We need to remove this file 
    async def _save_state(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        try:
            async with aiofiles.open(self.state_path, 'w', encoding='utf-8') as f:
                await f.write(self.state.model_dump_json(indent=2))
        except Exception as e:
            logger.error(f"Save Error: {e}")

    async def _load_state(self):
        try:
            db_state = await self.db_state_store.load_state(self.session.session_id)
            self.state = db_state
            if self.state.hypothesis.id:
                logger.info(
                    "Loaded planner state from DB for session %s (hypothesis=%s, plan_steps=%d)",
                    self.session.session_id,
                    self.state.hypothesis.id,
                    len(self.state.plan.steps),
                )
            else:
                logger.info(
                    "No persisted planner state found in DB for session %s",
                    self.session.session_id,
                )
        except Exception as e:
            logger.error(f"Load Error: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # RESPONSE PROCESSORS
    # ══════════════════════════════════════════════════════════════════════════

    async def _parse_llm_json(self, response_content: str) -> dict | None:
        clean_content = self._clean_json(response_content)

        for attempt, content in enumerate(
            [clean_content, self._fix_invalid_json_escapes(clean_content)]
        ):
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                if attempt == 0:
                    logger.warning(f"[Planner]: JSON attempt 1 failed ({e}), retrying with escape fix...")
                else:
                    logger.error(f"[Planner]: JSON parse error after escape fix: {e}")
                    logger.error(f"[Planner]: Clean content (first 300 chars): {clean_content[:300]}")

            return None

    async def _process_hypothesis_response(self, response_content: str):
        """Parse hypothesis JSON from the HypothesisArchitect and update state/UI."""
        logger.info(f"[HypothesisArchitect]: {response_content[:100]}...")
        
        data = await self._parse_llm_json(response_content)

        match data:
            case None:
                if display_text := response_content.replace("APPROVE", "").strip():
                    await self._send("msg", display_text) ##Here we send the message with the agent
                    return
            case {"hypothesis": dict() as hypothesis, **rest}:
                persisted_hypothesis = await self.session_service.handle_hypothesis_update(
                    self.session, hypothesis
                )
                if persisted_hypothesis is not None:
                    self.state.hypothesis = persisted_hypothesis
                else:
                    # Keep local planner state usable even when backend persistence fails.
                    self.state.hypothesis = self.state.hypothesis.model_copy(update=hypothesis)
                # Keep the in-memory session coherent even when backend calls fail.
                self.session.hypothesis = self.state.hypothesis
                # Always emit structured hypothesis updates so the start node renders immediately.
                await self._send("hypothesis", self.state.hypothesis.model_dump())
                if message := rest.get("message"):
                    await self._send("msg", message)
            case {"message": str() as message}:
                await self._send("msg", message)

        await self._save_state()

    async def _process_pipeline_response(self, response_content: str):
        """Parse pipeline plan JSON from the PipelineArchitect and update state/UI."""
        logger.info(f"[PipelineArchitect]: {response_content[:100]}...")

        clean_content = self._clean_json(response_content)

        # Try parsing — if invalid escapes (LaTeX, paths), fix and retry
        data = None
        for attempt, content in enumerate(
            [clean_content, self._fix_invalid_json_escapes(clean_content)]
        ):
            try:
                data = json.loads(content)
                break
            except json.JSONDecodeError as e:
                if attempt == 0:
                    logger.warning(f"Pipeline JSON attempt 1 failed ({e}), retrying with escape fix...")
                    continue
                logger.error(f"Pipeline JSON parse error after escape fix: {e}")
                # Avoid Windows cp1252 console encoding crashes on non-ASCII chars.
                preview = clean_content[:300].encode("ascii", errors="replace").decode("ascii")
                logger.error(f"Clean content (first 300 chars): {preview}")
                print(f"[DEBUG] Pipeline JSON parse error: {e}")
                print(f"[DEBUG] Clean content preview: {clean_content[:200]}...")

        if data is None:
            display_text = response_content.replace("APPROVE", "").strip()
            if display_text:
                await self._send("msg", display_text)
            return

        if "steps" in data and isinstance(data["steps"], list):
            persisted_plan = await self.session_service.handle_plan_update(
                self.session, data["steps"]
            )
            if persisted_plan is not None:
                self.state.plan = persisted_plan
            else:
                # Preserve in-memory plan so UX can continue despite transient backend errors.
                self.state.plan.steps = [
                    StudyStep(
                        title=s.get("title", ""),
                        description=s.get("description", ""),
                        module=s.get("id", ""),
                    )
                    for s in data["steps"]
                ]
                self.state.plan.study_goal = self.state.hypothesis.title

            # Keep in-memory session coherent even when backend plan persistence fails.
            self.session.plan = self.state.plan

            # Always emit plan steps so the planning canvas can render immediately.
            ui_steps = [
                {
                    "id": s.module,
                    "name": s.title,
                    "description": s.description,
                    "module": s.module,
                    "status": s.status,
                }
                for s in self.state.plan.steps
            ]
            await self._send(
                "plan",
                {
                    "study_goal": self.state.plan.study_goal or self.state.hypothesis.title,
                    "steps": ui_steps,
                },
            )

            # Store full instructions in task_messages for later use
            self._pipeline_instructions = {
                s.get("id", ""): s.get("instructions", "")
                for s in data['steps']
            }

        # Rationale is included in the plan payload for the canvas, not sent to chat

        if "message" in data and data["message"]:
            await self._send("msg", data["message"])

        await self._save_state()

    # ══════════════════════════════════════════════════════════════════════════
    # PICO EXTRACTION & KNOWLEDGE BUILDING
    # ══════════════════════════════════════════════════════════════════════════

    def _extract_pico_from_hypothesis(self) -> dict:
        """
        Extract a PICO dict from the confirmed hypothesis.
        Maps ClinicalHypothesis fields → knowledge_builder PICO format.
        """
        hyp = self.state.hypothesis

        def _to_str(val):
            if isinstance(val, dict):
                return ", ".join(f"{k}: {v}" for k, v in val.items())
            return str(val) if val else ""

        return {
            "population": _to_str(hyp.population),
            "intervention": _to_str(hyp.intervention),
            "comparison": _to_str(hyp.comparison),
            "outcome": _to_str(hyp.outcome),
            "hypothesis": hyp.hypothesis or hyp.title or "",
            "question": hyp.question or "",
        }

    async def _build_knowledge_base(self) -> tuple:
        """
        Run the knowledge builder (synchronous, CPU-bound) in a thread pool.
        Returns (knowledge_base, sources_guide, papers, knowledge_sections) tuple.
        Sends short progress logs to the frontend via WebSocket.
        """
        pico = self._extract_pico_from_hypothesis()
        logger.info(f"Building knowledge base with PICO: {pico}")

        loop = asyncio.get_event_loop()

        def progress_callback(message: str):
            """Thread-safe bridge: schedule WS send on the event loop."""
            asyncio.run_coroutine_threadsafe(
                self._send("lit_review_log", {"message": message, "status": "active"}),
                loop
            )

        knowledge_base, sources_guide, papers, knowledge_sections = await loop.run_in_executor(
            None,
            lambda: build_knowledge_base(
                pico,
                progress_callback=progress_callback,
                cache_dir=self.state_path.parent / ".knowledge_cache", ## the cache won't be changed in the future it's an on device information 
            ),
        )

        logger.info(
            f"Knowledge base built: {len(knowledge_base):,} chars, "
            f"sources guide: {len(sources_guide):,} chars, "
            f"papers collected: {len(papers)}, "
            f"sections: {list(knowledge_sections.keys())}"
        )

        self.state.knowledge_base = knowledge_base
        self.state.sources_guide = sources_guide
        self.state.knowledge_sections = knowledge_sections
        await self._save_state()

        return knowledge_base, sources_guide, papers, knowledge_sections

    async def _save_literature_context(
        self,
        knowledge_base: str,
        sources_guide: str,
        generated_context: Any = None,
    ):
        """
        Save the literature review context (knowledge base + sources guide)
        to the workspace as a structured JSON file that the reporter agent
        can read via the read_literature_context MCP tool.
        """
        workspace_dir = self.state_path.parent
        lit_context_path = workspace_dir / "literature_context.json"  #Here we will change the place we save the literature context 


        context_data = {
            "knowledge_base": knowledge_base,
            "sources_guide": sources_guide,
            "hypothesis": self.state.hypothesis.model_dump() if self.state.hypothesis else {},
        }
        if generated_context and isinstance(generated_context, dict):
            context_data.update(generated_context)
        
        try:
            async with aiofiles.open(lit_context_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(context_data, indent=2, ensure_ascii=False))
            logger.info(f"Literature context saved to {lit_context_path}")
        except Exception as e:
            logger.error(f"Failed to save literature context: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # MAIN RUN — THREE-PHASE PIPELINE
    # ══════════════════════════════════════════════════════════════════════════

    async def run(self, start_query: str = "Start", do_lit_review: bool = True):
        await self._load_state()
        print(f"Planner {start_query} state before run:", self.state)
        self._pipeline_instructions = {}

        # Only run if not already confirmed
        if self.state.hypothesis.state != "confirmed":
            print("state not confirmed, planning phases engaged")
            logger.info("state not confirmed, planning phases engaged session_id = %s", self.session.session_id)

            # ─────────────────────────────────────────────────────────────
            # PHASE 1: HYPOTHESIS STRUCTURING
            # ─────────────────────────────────────────────────────────────
            # await self._send("msg", "**Phase 1: Hypothesis Structuring**")

            hypothesis_architect = self.agents["hypothesis_architect"]

            # Get initial user input
            if start_query and not self.state.hypothesis.title: ##Potential problem
                user_input = start_query
            else:
                await self._send("msg", "Please enter your clinical observation or research idea.")
                user_input = await self.input_provider()

            # Generate initial PICO hypothesis
            prompt = (
                f"User's clinical observation: {user_input}\n\n"
                "Structure this into a full PICO hypothesis. "
                "Return JSON with 'hypothesis' and 'message' fields."
            )

            response = await hypothesis_architect.on_messages(
                [TextMessage(content=prompt, source="user")],
                cancellation_token=None
            )
            print(f"response from hypothesis_architect: {response.chat_message.content}")
            await self._process_hypothesis_response(response.chat_message.content)

            # Mark hypothesis confirmed
            self.state.hypothesis.state = "confirmed"
            await self.session_service.confirm_hypothesis(self.session)
            await self._send("hypothesis", self.state.hypothesis.model_dump())
            await self._save_state()
            await self._load_state()
            # ─────────────────────────────────────────────────────────────
            # PHASE 2: LITERATURE REVIEW & KNOWLEDGE BUILDING (user choice)
            # ─────────────────────────────────────────────────────────────
            knowledge_base = ""
            sources_guide = ""
            knowledge_sections = {}

            if do_lit_review:
                # Create the literature review agent node on the canvas
                await self._send("lit_review_node", True)
                await self._send("lit_review_log", {"message": "Starting literature review", "status": "active"})

                try:
                    knowledge_base, sources_guide, papers, knowledge_sections = await self._build_knowledge_base()

                    # Run full literature-review multi-agent pipeline using the
                    # prebuilt knowledge base to avoid duplicate builder calls.
                    workspace_dir = self.state_path.parent
                    lit_output_dir = workspace_dir / "literature_review_output"
                    await self._send("lit_review_log", {
                        "message": "Running literature review multi-agent pipeline",
                        "status": "active",
                    })
                    await run_literature_review(
                        pico=self._extract_pico_from_hypothesis(),
                        model=getattr(self.client, "model", None) or "deepseek-v3.1:671b-cloud",
                        output_dir=lit_output_dir,
                        cache_dir=workspace_dir / ".knowledge_cache",
                        prebuilt_knowledge={
                            "knowledge_base": knowledge_base,
                            "sources_guide": sources_guide,
                            "papers": papers,
                            "knowledge_sections": knowledge_sections,
                        },
                    )
                    
                    generated_context = {}
                    ranked_papers = []

                    context_path = lit_output_dir / "context.json"
                    if context_path.exists():
                        try:
                            generated_context = json.loads(context_path.read_text(encoding="utf-8"))
                        except Exception:
                            generated_context = {}

                    ranked_path = lit_output_dir / "ranked_papers.json"
                    if ranked_path.exists():
                        try:
                            ranked_papers = json.loads(ranked_path.read_text(encoding="utf-8"))
                        except Exception:
                            ranked_papers = []

                    # Save literature context to workspace for the reporter agent
                    await self._save_literature_context(
                        knowledge_base,
                        sources_guide,
                        generated_context=generated_context,
                    )

                    # Try to read generated literature report if available
                    report_md = ""
                    report_path = lit_output_dir / "literature_report.md"
                    if report_path.exists():
                        try:
                            report_md = report_path.read_text(encoding="utf-8")
                        except Exception:
                            pass

                    # Send the full literature output to the frontend for visualization
                    lit_payload: dict = {
                        "knowledge_base": knowledge_base,
                        "sources_guide": sources_guide,
                        "hypothesis": self.state.hypothesis.model_dump() if self.state.hypothesis else {},
                        "papers": papers,
                    }
                    if report_md:
                        lit_payload["report"] = report_md
                    if generated_context:
                        lit_payload["context"] = generated_context
                    if ranked_papers:
                        lit_payload["ranked_papers"] = ranked_papers
                    await self._send("lit_review_output", lit_payload)

                    await self._send("lit_review_done", True)

                    # Report knowledge base stats to user
                    kb_size = len(knowledge_base)
                    sg_size = len(sources_guide)
                    await self._send("msg", (
                        f"Knowledge base built successfully.\n"
                        f"- Domain knowledge: {kb_size:,} characters\n"
                        f"- Sources guide: {sg_size:,} characters\n\n"
                        "Proceeding to pipeline planning informed by literature evidence..."
                    ))
                except Exception as e:
                    logger.error(f"Knowledge building failed: {e}", exc_info=True)
                    await self._send("lit_review_done", True)
                    await self._send("msg", (
                        f"Warning: Knowledge base construction failed ({e}). "
                        "Pipeline planning will proceed without literature augmentation."
                    ))
            else:
                await self._send("msg", "Skipping literature review. Proceeding to pipeline planning...")

            # ─────────────────────────────────────────────────────────────
            # PHASE 3: DYNAMIC PIPELINE PLANNING
            # ─────────────────────────────────────────────────────────────
            # await self._send("msg", "**Phase 3: Pipeline Planning**")

            pipeline_architect = self.agents["pipeline_architect"]

            # Summarize knowledge for the pipeline architect — balanced
            # across all sections instead of blind truncation
            kb_summary = summarize_knowledge_for_planning(
                knowledge_sections, self.state.hypothesis
            )

            hypothesis_payload = self.state.hypothesis.model_dump()

            pipeline_prompt = (
                "CONFIRMED HYPOTHESIS (JSON):\n"
                f"{json.dumps(hypothesis_payload, ensure_ascii=False, indent=2)}\n\n"
                f"SUMMARIZED DOMAIN KNOWLEDGE (from literature review):\n{kb_summary}\n\n"
                "Use the confirmed hypothesis as the primary source of study intent. "
                "Do not ask for missing hypothesis details unless the hypothesis JSON is empty.\n\n"
                "Based on this hypothesis and the domain knowledge, design an optimal "
                "research pipeline. Select the agents needed, order them logically, and "
                "provide detailed, study-specific instructions for each agent. "
                "Return JSON with 'steps', 'rationale', and 'message'."
            )

            response = await pipeline_architect.on_messages(
                [TextMessage(content=pipeline_prompt, source="user")],
                cancellation_token=None
            )
            print(f"response from pipeline_architect: {response.chat_message.content}")
            await self._process_pipeline_response(response.chat_message.content)

            # Pipeline refinement loop
            while True:
                await self._send("plan_approval", {
                    "message": "Review the pipeline plan. Approve it or describe changes."
                })
                user_feedback = await self.input_provider()

                if "APPROVE" in user_feedback.upper():
                    break

                refinement_prompt = (
                    f"User Feedback: {user_feedback}\n\n"
                    "Update the pipeline plan based on their feedback. "
                    "If the user is only asking a question that doesn't change the plan, answer them thoroughly "
                    "without updating the plan. "
                    "You can add, remove, or reorder steps. Update instructions as needed. "
                    "Return JSON with updated 'steps' if updated, 'rationale', and 'message'."
                )

                response = await pipeline_architect.on_messages(
                    [TextMessage(content=refinement_prompt, source="user")],
                    cancellation_token=None
                )
                await self._process_pipeline_response(response.chat_message.content)

            # ─────────────────────────────────────────────────────────────
            # FINALIZE: Compile task messages with context augmentation
            # ─────────────────────────────────────────────────────────────
            await self._save_state()
            await self.session_service.confirm_plan(self.session)
            await self._load_state()
            await self._send("msg", "Research plan confirmed. You can excute the agents now...")

            # Initial LaTeX generation/emission and persistence removed.
            # The frontend or reporter agent should request or generate reports on demand.

            try:
                # Compile structured task messages with pre-structured knowledge sections
                task_messages = compile_task_messages(
                    hypothesis=self.state.hypothesis,
                    plan=self.state.plan,
                    knowledge_sections=knowledge_sections,
                    sources_guide=sources_guide,
                    pipeline_instructions=self._pipeline_instructions,
                )
                await self._save_state()
                await self.session_service.confirm_plan(self.session)

                # await self._send("msg", f"Generated {len(task_messages)} context-enriched task messages.")

                persisted_messages = await self.session_service.save_task_messages_for_plan(
                    self.session,
                    task_messages,
                )
                # await self._send(
                #     "msg",
                #     f"Persisted {len(persisted_messages)}/{len(task_messages)} task messages with context augmentation to DB.",
                # )

                # Send task message summaries to UI
                task_summaries = [
                    {
                        "task_id": tm.task_id,
                        "phase": tm.phase,
                        "agent_id": tm.agent_id,
                        "agent_title": tm.agent_title,
                        "has_knowledge": bool(tm.context_augmentation.knowledge_summary),
                        "has_literature": bool(tm.context_augmentation.literature_findings),
                        "has_methodology": bool(tm.context_augmentation.methodology_guidance),
                        "instructions_preview": tm.instructions[:150] + "..."
                            if len(tm.instructions) > 150 else tm.instructions,
                    }
                    for tm in task_messages
                ]
                await self._send("task_messages", task_summaries)
            except Exception as e:
                logger.error(f"Task message compilation failed (non-fatal): {e}", exc_info=True)
                await self._send("msg", f"Warning: Task message compilation failed ({e}). Pipeline will proceed with basic instructions.")

            await self._send("modules_matched", True)
