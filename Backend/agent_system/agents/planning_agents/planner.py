"""
Clinical Planner Module

Orchestrates the three-phase clinical research planning pipeline:
    Phase 1 — Hypothesis Structuring (PICO format)
    Phase 2 — Literature Review & Knowledge Building
    Phase 3 — Dynamic Pipeline Planning (knowledge-informed)

The ClinicalPlanner manages state persistence, agent orchestration, WebSocket
communication, and the full lifecycle from initial user query to confirmed
research plan with context-augmented task messages.
"""

import sys
import os
import json
import logging
import re
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Callable, Awaitable, Any, Optional

import aiofiles
import yaml

from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken

from services.session_service import SessionService
from models.plans import *
from models.session import Session

from .planning_agents import create_planner_agents, create_model_client
from .db_state_store import PlannerDbStateStore
from .context_compiler import (
    compile_task_messages,
    build_enriched_pipeline_prompt,
    summarize_knowledge_for_planning,
    _build_hypothesis_summary,
)

from agents.literature_review_agent.knowledge_builder import build_knowledge_base
from agents.literature_review_agent.literature_review_agent import run_literature_review

# ═══════════════════════════════════════════════════════════════════════════════
# TYPE ALIASES
# ═══════════════════════════════════════════════════════════════════════════════

InputProvider = Callable[[], Awaitable[str]]
OutputCallback = Callable[[str, Any], Awaitable[None]]

logger = logging.getLogger(__name__)

# Constants
MAX_REFINEMENT_ITERATIONS = 10
REFINEMENT_TIMEOUT_SECONDS = 300  # 5 minutes
MAX_WS_MESSAGE_SIZE = 64000  # 64KB limit for WebSocket messages


# ═══════════════════════════════════════════════════════════════════════════════
# CLINICAL PLANNER
# ═══════════════════════════════════════════════════════════════════════════════

class ClinicalPlanner:
    """
    Three-phase clinical research planner.

    Orchestrates the full planning lifecycle:
        1. Hypothesis Structuring — Converts user observations into rigorous
           PICO-formatted clinical hypotheses.
        2. Literature Review — Builds domain knowledge base from PubMed and
           other sources to inform pipeline decisions.
        3. Pipeline Planning — Dynamically selects and configures execution
           agents based on the confirmed hypothesis and literature evidence.

    Thread-safety: Uses asyncio.Lock to prevent concurrent run() calls
    for the same session.

    Attributes:
        input_provider (InputProvider): Async callable for user input collection.
        output_handler (OutputCallback): Async callable for WebSocket/UI output.
        client: LLM model client for agent communication.
        agents (dict): Dictionary of planning agents (hypothesis_architect,
                       pipeline_architect, user_proxy).
        state (PlannerState): Current planner state with hypothesis, plan,
                              knowledge base, and phase tracking.
        session (Session): Current user session with persistent identifiers.
        session_service (SessionService): Service for persisting session state.
        db_state_store (PlannerDbStateStore): Database-backed state persistence.
        log_path (Path): Path to the planner log file.
    """

    def __init__(
        self,
        input_provider: InputProvider,
        output_callback: OutputCallback,
        workspace_dir: str,
        api_key: str,
        session: Session,
        session_service: SessionService,
        model_client: Any = None,
    ):
        """
        Initialize the ClinicalPlanner.

        Args:
            input_provider: Async callable that prompts the user and returns input.
            output_callback: Async callable for sending structured messages to
                             the frontend via WebSocket.
            workspace_dir: Root directory for the planner workspace (state files,
                           logs, literature output).
            api_key: API key for the LLM model service.
            session: Current user session object.
            session_service: Service for persisting session state to the database.
            model_client: Optional pre-configured LLM client. If None, creates
                          a new client using the API key.

        Raises:
            OSError: If log or state directories cannot be created.
        """
        self.input_provider = input_provider
        self.output_handler = output_callback

        # ── Concurrency Control ─────────────────────────────────────────
        self._run_lock = asyncio.Lock()

        # ── Directory & Path Setup ──────────────────────────────────────
        root_dir = Path(__file__).parent.parent.parent
        log_dir = os.path.join(root_dir, "logs")
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = os.path.join(log_dir, f"planner_{timestamp}.log")
        self.log_path = Path(log_file)

        self.state_path = Path(workspace_dir) / "state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        # ── Model Client ────────────────────────────────────────────────
        if model_client is not None:
            self.client = model_client
        else:
            self.client = create_model_client(api_key or "")

        # ── Agents ──────────────────────────────────────────────────────
        self.agents = create_planner_agents(
            self.client,
            input_func=self._user_proxy_input_adapter,
        )

        # ── State & Session ─────────────────────────────────────────────
        self.state = PlannerState()
        self.db_state_store = PlannerDbStateStore()
        self.session = session
        self.session_service = session_service

        # ── Internal State ──────────────────────────────────────────────
        self._pipeline_instructions: dict = {}
        self._state_load_failed: bool = False

        # ── Initialize Logging ──────────────────────────────────────────
        self._initialize_logging()

    async def _validate_hypothesis(self, user_input: str, cancellation_token=None) -> dict:
        """
        Validates if the user input is a complete PICO hypothesis.
        Returns a dict with keys:
          - "valid": bool
          - "feedback": str (user-friendly explanation if invalid)
          - "extracted": dict or None (PICO components if valid)
        """
        prompt = f"""
You are a clinical research validator. Analyze the following user input:
"{user_input}"

Determine if it constitutes a complete PICO hypothesis for a radiomics study.
A valid hypothesis MUST explicitly contain:
- Population (P): e.g., patients with lung cancer, age group, stage.
- Intervention (I): e.g., CT radiomics, MRI texture analysis.
- Comparison (C): e.g., compared to standard staging, vs. histopathology.
- Outcome (O): e.g., overall survival, treatment response, recurrence.

If ALL four components are present (even implicitly), set "valid": true.
If missing ANY component, set "valid": false.
If invalid, provide concise, actionable feedback in "feedback" specifying which
components are missing and a brief suggestion on how to fix it.

Output ONLY valid JSON in the following format:
{{"valid": true/false, "feedback": "your feedback here", "extracted": {{}} or null}}
"""
        try:
            hypothesis_architect = self.agents["hypothesis_architect"]
            response = await self._call_agent_with_retry(
                hypothesis_architect,
                prompt,
                cancellation_token=cancellation_token,
            )
            data = await self._parse_llm_json(response.chat_message.content, log_prefix="[Validator]")
            
            if not data or "valid" not in data or "feedback" not in data:
                raise ValueError("Missing required keys")
            return data
        except Exception as e:
            logger.error("Validation error: %s", e)
            return {
                "valid": False,
                "feedback": "System error validating your input. Please rephrase clearly with Population, Intervention, Comparison, and Outcome.",
                "extracted": None
            }

    # ══════════════════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ══════════════════════════════════════════════════════════════════════

    async def _user_proxy_input_adapter(
        self,
        prompt: str = None,
        cancellation_token: Any = None,
    ) -> str:
        """
        Adapter that bridges the UserProxyAgent's input function to the
        planner's input provider.

        Args:
            prompt: Optional prompt text (unused, passed through to provider).
            cancellation_token: Optional cancellation token (unused).

        Returns:
            User input string from the input provider.
        """
        return await self.input_provider()

    def _initialize_logging(self) -> None:
        """
        Set up dual logging handlers (file + stdout) for the planner.

        IMPORTANT: Only adds handlers if they don't already exist to prevent
        duplicate log messages and memory leaks from repeated instantiation.
        """
        if not logger.handlers:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

            file_handler = logging.FileHandler(self.log_path)
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )

            logger.addHandler(file_handler)
            logger.setLevel(logging.INFO)

    async def initialize(self) -> None:
        """
        Initialize the planner by loading persisted state from the database.

        Should be called once after construction and before run().
        """
        await self._load_state()

    async def _send(
        self,
        type_: str,
        data: Any,
        session_id: str = None,
    ) -> None:
        """
        Send a structured message to the frontend via the output callback.

        For large payloads (lit_review_output), automatically truncates
        oversized fields to prevent WebSocket connection drops.

        Args:
            type_: Message type identifier (e.g., "msg", "hypothesis", "plan").
            data: Message payload (string, dict, or any serializable object).
            session_id: Optional session ID override. Defaults to current session.
        """
        # Check payload size for literature review output
        if type_ == "lit_review_output" and isinstance(data, dict):
            try:
                payload_size = len(json.dumps(data, ensure_ascii=False))
                if payload_size > MAX_WS_MESSAGE_SIZE:
                    logger.warning(
                        "Literature review payload too large (%d bytes), "
                        "truncating knowledge base fields",
                        payload_size,
                    )
                    # Truncate the largest fields to prevent WS disconnection
                    if "knowledge_base" in data:
                        data["knowledge_base"] = (
                            data["knowledge_base"][:50000]
                            + "\n\n[... Truncated for WebSocket — full version in workspace ...]"
                        )
                    if "sources_guide" in data:
                        data["sources_guide"] = (
                            data["sources_guide"][:30000]
                            + "\n\n[... Truncated for WebSocket — full version in workspace ...]"
                        )
            except Exception:
                pass  # Best-effort size check

        await self.output_handler(type_, data, self.session.session_id)

    # ══════════════════════════════════════════════════════════════════════
    # JSON PARSING UTILITIES
    # ══════════════════════════════════════════════════════════════════════

    def _clean_json(self, text: str) -> str:
        """
        Extract JSON from LLM response, handling markdown code fences.

        Handles several common LLM output patterns:
            - ```json { ... } ```
            - ``` { ... } ```
            - Leading/trailing ```json or ```
            - Raw JSON with no fences
            - APPROVED text appended to JSON

        Args:
            text: Raw LLM response text.

        Returns:
            Cleaned JSON string ready for parsing.
        """
        text = text.strip()
        text = text.replace("APPROVE", "")

        # Pattern 1: Fenced JSON with language specifier
        fence_match = re.search(r"```json\s*(\{.*\})\s*```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()

        # Pattern 2: Fenced code block without language specifier
        fence_match = re.search(r"```\s*(\{.*\})\s*```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()

        # Pattern 3: Leading/trailing fence markers
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```", "", text)
        text = re.sub(r"```$", "", text)

        # Pattern 4: Find the first complete JSON object
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            return brace_match.group(0).strip()

        return text.strip()

    @staticmethod
    def _fix_invalid_json_escapes(text: str) -> str:
        """
        Fix invalid backslash escapes that break json.loads().

        LLMs often embed LaTeX (\\documentclass, \\begin{table}) or Windows
        paths (\\Users) in JSON string values. These are not valid JSON
        escape sequences and cause ``Invalid \\escape`` errors.

        Valid JSON escapes: \\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX

        Any backslash NOT followed by one of those characters is doubled so
        json.loads() will accept it.

        Args:
            text: JSON string that may contain invalid escapes.

        Returns:
            JSON string with invalid backslashes properly escaped.
        """
        return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)

    # ══════════════════════════════════════════════════════════════════════
    # STATE PERSISTENCE
    # ══════════════════════════════════════════════════════════════════════

    async def _save_state(self) -> None:
        """
        Persist the current planner state to disk as JSON.

        The state file is written atomically to the workspace directory.
        Errors are logged but not raised — state persistence failures
        are non-fatal to allow the pipeline to continue.
        """
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        try:
            async with aiofiles.open(self.state_path, "w", encoding="utf-8") as f:
                await f.write(self.state.model_dump_json(indent=2))
        except Exception as e:
            logger.error("Save Error: %s", e)

    async def _load_state(self) -> None:
        """
        Load planner state from the database via the state store.

        If no persisted state exists for the current session, a fresh
        PlannerState is used. If loading fails, sets _state_load_failed
        flag to prevent accidental overwrites.

        Raises:
            RuntimeError: If state loading fails critically (DB unreachable).
        """
        try:
            db_state = await self.db_state_store.load_state(self.session.session_id)
            self.state = db_state
            self._state_load_failed = False

            if self.state.hypothesis.id:
                logger.info(
                    "Loaded planner state from DB for session %s "
                    "(hypothesis=%s, plan_steps=%d)",
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
            logger.error("Load Error: %s", e)
            self._state_load_failed = True
            # Don't silently continue — notify the caller
            raise RuntimeError(
                f"Failed to load planner state for session {self.session.session_id}"
            ) from e

    # ══════════════════════════════════════════════════════════════════════
    # LLM RESPONSE PROCESSING
    # ══════════════════════════════════════════════════════════════════════

    async def _parse_llm_json(
        self,
        response_content: str,
        log_prefix: str = "[Planner]",
    ) -> dict | None:
        """
        Robustly parse JSON from LLM responses with multiple fallback strategies.

        Attempts, in order:
            1. Direct JSON parsing after cleaning.
            2. JSON parsing with escape fixes applied.
            3. YAML fallback parsing for near-JSON content.

        Args:
            response_content: Raw response text from the LLM.
            log_prefix: Prefix for log messages to identify the source agent.

        Returns:
            Parsed dictionary, or None if all parsing strategies fail.
        """
        clean_content = self._clean_json(response_content)

        for attempt, content in enumerate([
            clean_content,
            self._fix_invalid_json_escapes(clean_content),
        ]):
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                if attempt == 0:
                    logger.warning(
                        "%s: JSON attempt 1 failed (%s), retrying with escape fix...",
                        log_prefix, e,
                    )
                    continue
                else:
                    logger.error(
                        "%s: JSON parse error after escape fix: %s",
                        log_prefix, e,
                    )

                    # FALLBACK: Try YAML parsing for near-JSON content
                    try:
                        logger.warning(
                            "%s: Attempting yaml.safe_load as fallback...",
                            log_prefix,
                        )
                        data = yaml.safe_load(content)
                        if data and isinstance(data, dict):
                            logger.info(
                                "%s: Successfully parsed using yaml fallback",
                                log_prefix,
                            )
                            return data
                    except Exception as yaml_e:
                        logger.error(
                            "%s: YAML fallback also failed: %s",
                            log_prefix, yaml_e,
                        )

                    preview = clean_content[:300].encode(
                        "ascii", errors="replace"
                    ).decode("ascii")
                    logger.error(
                        "%s: Clean content preview: %s",
                        log_prefix, preview,
                    )

        return None

    async def _process_hypothesis_response(self, response_content: str) -> None:
        """
        Parse hypothesis JSON from the HypothesisArchitect and update state/UI.

        Handles three response patterns:
            - Full hypothesis with PICO fields → persists and emits to UI.
            - Message-only response → forwards to chat.
            - Parse failure → sends error message to chat.

        Args:
            response_content: Raw response text from the HypothesisArchitect.
        """
        logger.info("[HypothesisArchitect]: %s...", response_content[:100])

        data = await self._parse_llm_json(
            response_content,
            log_prefix="[HypothesisArchitect]",
        )

        match data:
            case None:
                logger.error(
                    "Failed to parse HypothesisArchitect response as JSON. "
                    "Sending error message to chat."
                )
                await self._send(
                    "msg",
                    "The agent generated an improperly formatted hypothesis. "
                    "Please ask it to regenerate or revise your request.",
                )
                return

            case {"hypothesis": dict() as hypothesis, **rest}:
                persisted_hypothesis = await self.session_service.handle_hypothesis_update(
                    self.session, hypothesis
                )
                if persisted_hypothesis is not None:
                    self.state.hypothesis = persisted_hypothesis
                else:
                    # Keep local planner state usable even when backend persistence fails
                    self.state.hypothesis = self.state.hypothesis.model_copy(
                        update=hypothesis
                    )

                # Keep in-memory session coherent
                self.session.hypothesis = self.state.hypothesis

                # Always emit structured hypothesis updates so the UI renders immediately
                await self._send(
                    "hypothesis",
                    self.state.hypothesis.model_dump(),
                )

                if message := rest.get("message"):
                    await self._send("msg", message)

            case {"message": str() as message}:
                await self._send("msg", message)

        await self._save_state()

    async def _process_pipeline_response(self, response_content: str) -> None:
        """
        Parse pipeline plan JSON from the PipelineArchitect and update state/UI.

        Extracts steps with instructions, persists the plan to the database,
        and emits structured plan updates to the UI canvas.

        Args:
            response_content: Raw response text from the PipelineArchitect.
        """
        logger.info("[PipelineArchitect]: %s...", response_content[:100])

        data = await self._parse_llm_json(
            response_content,
            log_prefix="[PipelineArchitect]",
        )

        if data is None:
            logger.error(
                "Failed to parse PipelineArchitect response as JSON. "
                "Sending error message to chat."
            )
            await self._send(
                "msg",
                "The agent generated an improperly formatted plan. "
                "Please ask it to regenerate or revise your request.",
            )
            return

        if "steps" in data and isinstance(data["steps"], list):
            persisted_plan = await self.session_service.handle_plan_update(
                self.session, data["steps"]
            )

            if persisted_plan is not None:
                self.state.plan = persisted_plan
            else:
                # Preserve in-memory plan so UX can continue despite transient
                # backend errors
                self.state.plan.steps = [
                    StudyStep(
                        title=s.get("title", ""),
                        description=s.get("description", ""),
                        module=s.get("id", ""),
                    )
                    for s in data["steps"]
                ]
                self.state.plan.study_goal = self.state.hypothesis.title

            # Keep in-memory session coherent
            self.session.plan = self.state.plan

            # Emit plan steps for the planning canvas
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
                "pipeline_update",
                {
                    "study_goal": (
                        self.state.plan.study_goal or self.state.hypothesis.title
                    ),
                    "steps": ui_steps,
                },
            )
            # Also emit old "plan" for backwards compatibility if needed
            await self._send(
                "plan",
                {
                    "study_goal": (
                        self.state.plan.study_goal or self.state.hypothesis.title
                    ),
                    "steps": ui_steps,
                },
            )

            # Store full instructions for later context augmentation
            self._pipeline_instructions = {
                s.get("id", ""): s.get("instructions", "")
                for s in data["steps"]
            }

        # Forward any accompanying message to chat
        if "message" in data and data["message"]:
            await self._send("msg", data["message"])

        await self._save_state()

    # ══════════════════════════════════════════════════════════════════════
    # PICO EXTRACTION & KNOWLEDGE BUILDING
    # ══════════════════════════════════════════════════════════════════════

    def _extract_pico_from_hypothesis(self) -> dict:
        """
        Extract a PICO dict from the confirmed hypothesis.

        Maps ClinicalHypothesis model fields to the knowledge_builder's
        expected PICO format.

        Returns:
            Dictionary with keys: population, intervention, comparison,
            outcome, hypothesis, question.
        """
        hyp = self.state.hypothesis

        def _to_str(val: Any) -> str:
            """Convert a value to string, handling nested dicts."""
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

        Executes the literature search, paper collection, and knowledge base
        construction. Sends progress logs to the frontend via WebSocket.

        Returns:
            Tuple of (knowledge_base, sources_guide, papers, knowledge_sections).

        Raises:
            Exception: Propagated from build_knowledge_base — caught by caller.
        """
        pico = self._extract_pico_from_hypothesis()
        logger.info("Building knowledge base with PICO: %s", pico)

        loop = asyncio.get_event_loop()

        def progress_callback(message: str) -> None:
            """
            Thread-safe bridge: schedule WebSocket send on the event loop.

            Called from worker threads during knowledge base construction.
            """
            asyncio.run_coroutine_threadsafe(
                self._send(
                    "lit_review_log",
                    {"message": message, "status": "active"},
                ),
                loop,
            )

        # Run CPU-bound knowledge building in executor
        knowledge_base, sources_guide, papers, knowledge_sections = (
            await loop.run_in_executor(
                None,
                lambda: build_knowledge_base(
                    pico,
                    progress_callback=progress_callback,
                    cache_dir=self.state_path.parent / ".knowledge_cache",
                ),
            )
        )

        logger.info(
            "Knowledge base built: %s chars, sources guide: %s chars, "
            "papers: %d, sections: %s",
            len(knowledge_base),
            len(sources_guide),
            len(papers),
            list(knowledge_sections.keys()),
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
    ) -> None:
        """
        Save the literature review context to the workspace as a structured
        JSON file for downstream agents.

        The reporter agent reads this file via the read_literature_context
        MCP tool to access domain knowledge for manuscript generation.

        Args:
            knowledge_base: Full domain knowledge text.
            sources_guide: Bibliography and sources reference.
            generated_context: Optional additional context from the literature
                               review multi-agent pipeline.
        """
        workspace_dir = self.state_path.parent
        lit_context_path = workspace_dir / "literature_context.json"

        context_data = {
            "knowledge_base": knowledge_base,
            "sources_guide": sources_guide,
            "hypothesis": (
                self.state.hypothesis.model_dump()
                if self.state.hypothesis else {}
            ),
        }
        if generated_context and isinstance(generated_context, dict):
            context_data.update(generated_context)

        try:
            async with aiofiles.open(
                lit_context_path, "w", encoding="utf-8"
            ) as f:
                await f.write(
                    json.dumps(context_data, indent=2, ensure_ascii=False)
                )
            logger.info("Literature context saved to %s", lit_context_path)
        except Exception as e:
            logger.error("Failed to save literature context: %s", e)

    # ══════════════════════════════════════════════════════════════════════
    # RETRY LOGIC FOR LLM CALLS
    # ══════════════════════════════════════════════════════════════════════

    async def _call_agent_with_retry(
        self,
        agent,
        prompt: str,
        max_retries: int = 3,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> Any:
        """
        Call an agent with exponential backoff retry logic.

        LLM APIs can be unreliable — this wrapper provides resilience
        against temporary failures, rate limits, and timeouts.

        Args:
            agent: The AutoGen agent to call.
            prompt: The text prompt to send.
            max_retries: Maximum number of retry attempts.
            cancellation_token: Optional cancellation token for cooperative
                                cancellation.

        Returns:
            The agent's response object.

        Raises:
            Exception: Re-raises the last exception if all retries are exhausted.
        """
        last_exception = None
        for attempt in range(max_retries):
            try:
                response = await agent.on_messages(
                    [TextMessage(content=prompt, source="user")],
                    cancellation_token=cancellation_token,
                )
                return response
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                    logger.warning(
                        "Agent call failed (attempt %d/%d): %s. "
                        "Retrying in %ds...",
                        attempt + 1, max_retries, e, wait_time,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(
                        "Agent call failed after %d attempts: %s",
                        max_retries, e,
                    )

        raise last_exception

    # ══════════════════════════════════════════════════════════════════════
    # MAIN RUN — THREE-PHASE PIPELINE
    # ══════════════════════════════════════════════════════════════════════

    async def run(
        self,
        start_query: str = "Start",
        do_lit_review: bool = True,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> None:
        """
        Execute the full three-phase clinical research planning pipeline.

        Phases:
            1. Hypothesis Structuring — Converts user query to PICO hypothesis
               using the HypothesisArchitect agent. Loops until confirmed.
            2. Literature Review — Builds domain knowledge base from PubMed
               and runs multi-agent literature review pipeline.
            3. Pipeline Planning — Dynamically designs the research execution
               plan using the PipelineArchitect agent. Supports refinement
               loop (max {MAX_REFINEMENT_ITERATIONS} iterations with
               {REFINEMENT_TIMEOUT_SECONDS}s timeout) until user approves.

        After all phases complete, compiles context-augmented task messages
        for downstream execution agents.

        Thread-safety: Uses asyncio.Lock to prevent concurrent execution
        for the same session.

        Args:
            start_query: Initial user query or clinical observation.
            do_lit_review: If True, runs the literature review phase.
                           If False, skips directly to pipeline planning.
            cancellation_token: Optional token for cooperative cancellation
                                of long-running operations.

        Raises:
            RuntimeError: If state loading fails.
            Exception: Re-raised from agent calls if all retries exhausted.
        """
        # ── Concurrency Guard ─────────────────────────────────────────
        async with self._run_lock:
            await self._load_state()
            logger.info(
                "Planner %s state before run: %s",
                start_query, self.state,
            )
            self._pipeline_instructions = {}

            # Exit early if hypothesis is already confirmed
            if self.state.hypothesis.state == "confirmed":
                logger.info(
                    "Hypothesis already confirmed for session %s, skipping planning",
                    self.session.session_id,
                )
                return

            logger.info(
                "State not confirmed, planning phases engaged (session_id=%s)",
                self.session.session_id,
            )

            # ══════════════════════════════════════════════════════════════
            # PHASE 1: HYPOTHESIS STRUCTURING
            # ══════════════════════════════════════════════════════════════

            hypothesis_architect = self.agents["hypothesis_architect"]
            
            # --- Phase 1: Hypothesis Validation with retry ---
            max_retries = 3
            retry_count = 0
            hypothesis_valid = False

            while not hypothesis_valid and retry_count < max_retries:
                # Determine user input
                if start_query and not self.state.hypothesis.title:
                    user_input = start_query
                    start_query = None # Clear it so next retry asks user
                else:
                    await self._send(
                        "msg",
                        "Please enter your clinical observation or research idea.",
                    )
                    user_input = await self.input_provider()

                # Run validation
                validation_result = await self._validate_hypothesis(user_input, cancellation_token=cancellation_token)

                if validation_result["valid"]:
                    # We have a valid hypothesis. Now we can formally extract and persist it using the architect.
                    # Or if extracted is full, we can just process it.
                    # The user's code suggests updating self.hypothesis, but we need to persist it.
                    prompt = (
                        f"User's clinical observation: {user_input}\n\n"
                        "Structure this into a full PICO hypothesis. "
                        "Return JSON with 'hypothesis' and 'message' fields."
                    )
                    response = await self._call_agent_with_retry(
                        hypothesis_architect,
                        prompt,
                        cancellation_token=cancellation_token,
                    )
                    logger.info("HypothesisArchitect response: %s", response.chat_message.content[:200])
                    await self._process_hypothesis_response(response.chat_message.content)
                    
                    self.state.hypothesis.state = "confirmed"
                    await self.session_service.confirm_hypothesis(self.session)
                    
                    hypothesis_dump = self.state.hypothesis.model_dump()
                    await self._send("hypothesis", hypothesis_dump)
                    await self._save_state()
                    
                    hypothesis_valid = True
                    break
                else:
                    retry_count += 1
                    feedback = validation_result["feedback"]
                    if retry_count >= max_retries:
                        error_msg = (
                            f"❌ I've asked {max_retries} times, but the input is still not a valid PICO hypothesis. "
                            f"Please restart the research process with a clear hypothesis.\n"
                            f"Last feedback: {feedback}"
                        )
                        await self._send("msg", error_msg)
                        return
                    else:
                        feedback_msg = f"⚠️ Invalid hypothesis: {feedback}\nPlease try again. ({retry_count}/{max_retries})"
                        await self._send("msg", feedback_msg)

            # ══════════════════════════════════════════════════════════════
            # PHASE 2: LITERATURE REVIEW & KNOWLEDGE BUILDING
            # ══════════════════════════════════════════════════════════════

            knowledge_base = ""
            sources_guide = ""
            knowledge_sections = {}

            if do_lit_review:
                # Create literature review agent node on the canvas
                await self._send("lit_review_node", True)
                await self._send(
                    "lit_review_log",
                    {"message": "Starting literature review", "status": "active"},
                )

                try:
                    # Build knowledge base
                    knowledge_base, sources_guide, papers, knowledge_sections = (
                        await self._build_knowledge_base()
                    )

                    # Run multi-agent literature review pipeline
                    workspace_dir = self.state_path.parent
                    lit_output_dir = workspace_dir / "literature_review_output"

                    await self._send(
                        "lit_review_log",
                        {
                            "message": "Running literature review multi-agent pipeline",
                            "status": "active",
                        },
                    )

                    await run_literature_review(
                        pico=self._extract_pico_from_hypothesis(),
                        model=getattr(self.client, "model", None)
                        or "deepseek-v3.1:671b-cloud",
                        output_dir=lit_output_dir,
                        cache_dir=workspace_dir / ".knowledge_cache",
                        prebuilt_knowledge={
                            "knowledge_base": knowledge_base,
                            "sources_guide": sources_guide,
                            "papers": papers,
                            "knowledge_sections": knowledge_sections,
                        },
                    )

                    # ── Load Literature Output Files ──────────────────
                    generated_context = {}
                    ranked_papers = []

                    context_path = lit_output_dir / "context.json"
                    if context_path.exists():
                        try:
                            generated_context = json.loads(
                                context_path.read_text(encoding="utf-8")
                            )
                        except Exception:
                            generated_context = {}

                    ranked_path = lit_output_dir / "ranked_papers.json"
                    if ranked_path.exists():
                        try:
                            ranked_papers = json.loads(
                                ranked_path.read_text(encoding="utf-8")
                            )
                        except Exception:
                            ranked_papers = []

                    # Save literature context for the reporter agent
                    await self._save_literature_context(
                        knowledge_base,
                        sources_guide,
                        generated_context=generated_context,
                    )

                    # Try to read generated literature report
                    report_md = ""
                    report_path = lit_output_dir / "literature_report.md"
                    if report_path.exists():
                        try:
                            report_md = report_path.read_text(encoding="utf-8")
                        except Exception:
                            pass

                    # ── Send Literature Output to Frontend ────────────
                    lit_payload: dict = {
                        "knowledge_base": knowledge_base,
                        "sources_guide": sources_guide,
                        "hypothesis": hypothesis_dump,
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
                    await self._send(
                        "msg",
                        (
                            f"Knowledge base built successfully.\n"
                            f"- Domain knowledge: {kb_size:,} characters\n"
                            f"- Sources guide: {sg_size:,} characters\n\n"
                            "Proceeding to pipeline planning informed by "
                            "literature evidence..."
                        ),
                    )

                except Exception as e:
                    logger.error(
                        "Knowledge building failed: %s", e, exc_info=True
                    )
                    await self._send("lit_review_done", True)
                    await self._send(
                        "msg",
                        (
                            f"Warning: Knowledge base construction failed ({e}). "
                            "Pipeline planning will proceed without literature "
                            "augmentation."
                        ),
                    )
            else:
                await self._send(
                    "msg",
                    "Skipping literature review. Proceeding to pipeline planning...",
                )

            # ══════════════════════════════════════════════════════════════
            # PHASE 3: DYNAMIC PIPELINE PLANNING
            # ══════════════════════════════════════════════════════════════

            pipeline_architect = self.agents["pipeline_architect"]

            # Summarize knowledge for the pipeline architect — balanced across
            # all sections instead of blind truncation
            kb_summary = summarize_knowledge_for_planning(
                knowledge_sections, self.state.hypothesis
            )

            pipeline_prompt = (
                "CONFIRMED HYPOTHESIS (JSON):\n"
                f"{json.dumps(hypothesis_dump, ensure_ascii=False, indent=2)}\n\n"
                f"SUMMARIZED DOMAIN KNOWLEDGE (from literature review):\n"
                f"{kb_summary}\n\n"
                "Use the confirmed hypothesis as the primary source of study intent. "
                "Do not ask for missing hypothesis details unless the hypothesis "
                "JSON is empty.\n\n"
                "Based on this hypothesis and the domain knowledge, design an optimal "
                "research pipeline. Select the agents needed, order them logically, and "
                "provide detailed, study-specific instructions for each agent. "
                "Return JSON with 'steps', 'rationale', and 'message'."
            )

            response = await self._call_agent_with_retry(
                pipeline_architect,
                pipeline_prompt,
                cancellation_token=cancellation_token,
            )
            logger.info(
                "PipelineArchitect response: %s",
                response.chat_message.content[:200],
            )
            await self._process_pipeline_response(response.chat_message.content)

            # ── Pipeline Revision Loop (Infinite) ──────────
            while True:
                # ══════════════════════════════════════════════════════════════
                # FINALIZE: Compile task messages with context augmentation
                # ══════════════════════════════════════════════════════════════
                try:
                    task_messages = compile_task_messages(
                        hypothesis=self.state.hypothesis,
                        plan=self.state.plan,
                        knowledge_sections=knowledge_sections,
                        sources_guide=sources_guide,
                        pipeline_instructions=self._pipeline_instructions,
                    )
                    await self.session_service.confirm_plan(self.session)
                    await self.session_service.save_task_messages_for_plan(
                        self.session,
                        task_messages,
                    )
                    task_summaries = [
                        {
                            "task_id": tm.task_id,
                            "phase": tm.phase,
                            "agent_id": tm.agent_id,
                            "agent_title": tm.agent_title,
                            "has_knowledge": bool(tm.context_augmentation.knowledge_summary),
                            "has_literature": bool(tm.context_augmentation.literature_findings),
                            "has_methodology": bool(tm.context_augmentation.methodology_guidance),
                            "instructions_preview": (
                                tm.instructions[:150] + "..."
                                if len(tm.instructions) > 150
                                else tm.instructions
                            ),
                        }
                        for tm in task_messages
                    ]
                    await self._send("task_messages", task_summaries)
                except Exception as e:
                    logger.error("Task message compilation failed (non-fatal): %s", e, exc_info=True)

                await self._save_state()
                await self._send("planning_complete", True)
                await self._send("modules_matched", True)
                
                try:
                    # Wait indefinitely for chat-based revisions
                    user_feedback = await self.input_provider()
                except Exception as e:
                    logger.info("Input provider stopped, ending planner task: %s", e)
                    break

                # Process user chat feedback
                refinement_prompt = (
                    f"User Feedback: {user_feedback}\n\n"
                    "Update the pipeline plan based on their feedback. "
                    "If the user is only asking a question that doesn't change "
                    "the plan, answer them thoroughly without updating the plan. "
                    "You can add, remove, or reorder steps. Update instructions as "
                    "needed. Return JSON with updated 'steps' if updated, "
                    "'rationale', and 'message'."
                )

                try:
                    response = await self._call_agent_with_retry(
                        pipeline_architect,
                        refinement_prompt,
                        cancellation_token=cancellation_token,
                    )
                    await self._process_pipeline_response(response.chat_message.content)
                except Exception as e:
                    logger.error("Error generating revised pipeline: %s", e)
                    await self._send("msg", f"Sorry, an error occurred while revising the pipeline: {e}")
                    await self._send("planning_failed", str(e))
                    continue # Try again on next input