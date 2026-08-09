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
import copy
from datetime import datetime
from pathlib import Path
from typing import Callable, Awaitable, Any, Optional, Tuple, List, Dict

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
from ..clinical_roi_policy import (
    ROI_IDENTITY_POLICY,
)

from agents.literature_review_agent.knowledge_builder import build_knowledge_base
from agents.literature_review_agent.literature_review_agent import run_literature_review
from storage_paths import LOGS_DIR, workspace_subpath

# ═══════════════════════════════════════════════════════════════════════════════
# TYPE ALIASES
# ═══════════════════════════════════════════════════════════════════════════════

InputProvider = Callable[[], Awaitable[str]]
OutputCallback = Callable[[str, Any], Awaitable[None]]

# Constants
MAX_WS_MESSAGE_SIZE = 64000  # 64KB limit for WebSocket messages
WS_TRUNCATION_MARKER = "\n\n[... Truncated for WebSocket; full version is in the workspace ...]"


def _json_size_bytes(value: Any) -> int:
    """Return the UTF-8 byte size of a JSON-serializable value."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _fit_literature_payload(
    data: dict[str, Any],
    max_bytes: int = MAX_WS_MESSAGE_SIZE,
) -> dict[str, Any]:
    """Return a non-mutating literature payload that fits the WS byte cap."""
    payload = copy.deepcopy(data)
    if _json_size_bytes(payload) <= max_bytes:
        return payload

    truncated_fields: set[str] = set()
    target_bytes = max(1024, max_bytes - 512)
    minimum_text_chars = {
        "knowledge_base": 6000,
        "sources_guide": 1500,
        "report": 6000,
    }

    while _json_size_bytes(payload) > target_bytes:
        text_candidates = [
            (key, value)
            for key, value in payload.items()
            if isinstance(value, str)
            and len(value)
            > minimum_text_chars.get(key, 1000) + len(WS_TRUNCATION_MARKER)
        ]
        if text_candidates:
            key, value = max(
                text_candidates,
                key=lambda item: len(item[1].encode("utf-8")),
            )
            minimum = minimum_text_chars.get(key, 1000)
            new_length = max(minimum, len(value) // 2)
            payload[key] = value[:new_length] + WS_TRUNCATION_MARKER
            truncated_fields.add(key)
            continue

        list_candidates = [
            (key, value)
            for key, value in payload.items()
            if isinstance(value, list) and len(value) > 5
        ]
        if list_candidates:
            key, value = max(
                list_candidates,
                key=lambda item: _json_size_bytes(item[1]),
            )
            payload[key] = value[: max(5, len(value) // 2)]
            truncated_fields.add(key)
            continue

        removable = next(
            (
                key
                for key in (
                    "context",
                    "ranked_papers",
                    "report",
                    "knowledge_base",
                    "sources_guide",
                )
                if key in payload
            ),
            None,
        )
        if removable is None:
            break
        payload.pop(removable)
        truncated_fields.add(removable)

    if truncated_fields:
        payload["_truncated"] = {
            "fields": sorted(truncated_fields),
            "full_version": "workspace",
        }

    if _json_size_bytes(payload) > max_bytes:
        # Last-resort envelope: retain the PICO and a small paper sample rather
        # than sending a frame large enough to disconnect the WebSocket.
        payload = {
            "hypothesis": payload.get("hypothesis", {}),
            "papers": list(payload.get("papers") or [])[:5],
            "_truncated": {
                "fields": sorted(set(data) - {"hypothesis"}),
                "full_version": "workspace",
            },
        }
        while payload["papers"] and _json_size_bytes(payload) > max_bytes:
            payload["papers"].pop()

    if _json_size_bytes(payload) > max_bytes:
        payload = {
            "_truncated": {
                "fields": sorted(data),
                "full_version": "workspace",
            }
        }

    return payload


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
        log_dir = LOGS_DIR / "agent-system"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        session_tag = str(session.session_id).replace("/", "_").replace("\\", "_")
        log_file = os.path.join(
            log_dir,
            f"planner_{timestamp}_{session_tag}.log",
        )
        self.log_path = Path(log_file)

        self.workspace_dir = Path(workspace_dir)
        self.state_path = workspace_subpath(self.workspace_dir, "state.json")
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
        self.logger = logging.getLogger(
            f"{__name__}.session.{session.session_id}.{id(self)}"
        )

        # ── Internal State ──────────────────────────────────────────────
        self._pipeline_instructions: dict = {}
        self._state_load_failed: bool = False

        # ── Initialize Logging ──────────────────────────────────────────
        self._initialize_logging()



    # ══════════════════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ══════════════════════════════════════════════════════════════════════

    def _clean_knowledge_for_ui(self, raw_text: str) -> str:
        """
        Convert the ASCII-boxed knowledge base into clean Markdown for the UI.
        Removes box-drawing characters, trims lines, and converts ALL CAPS
        section headers into Markdown headings (##).
        """
        import re
        if not raw_text:
            return ""

        lines = raw_text.splitlines()
        clean_lines = []

        for line in lines:
            # Remove leading/trailing box-drawing characters and spaces
            line = re.sub(r'^[│┌└─]+', '', line).strip()
            line = re.sub(r'[│┌└─]+$', '', line).strip()

            if not line:
                continue

            # Detect section headers (e.g., "ANATOMY, PATHOLOGY & CLINICAL CONTEXT")
            # They are typically all caps, contain commas/ampersands, and are longer than 10 chars.
            if re.match(r'^[A-Z][A-Z\s,&\-]+$', line) and len(line) > 10:
                clean_lines.append(f"\n## {line}\n")
            else:
                clean_lines.append(line)

        return "\n".join(clean_lines)

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

        Each planner instance owns a uniquely named logger and file handler so
        concurrent sessions cannot leak records into one another's files.
        """
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

        file_handler = logging.FileHandler(self.log_path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )

        self.logger.addHandler(file_handler)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = True

    async def initialize(self) -> None:
        """
        Initialize the planner by loading persisted state from the database.

        Should be called once after construction and before run().
        """
        await self._load_state()

    def close(self) -> None:
        """Close this planner's private file handlers."""
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            handler.close()

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
                payload_size = _json_size_bytes(data)
                if payload_size > MAX_WS_MESSAGE_SIZE:
                    fitted_data = _fit_literature_payload(data)
                    fitted_size = _json_size_bytes(fitted_data)
                    self.logger.warning(
                        "Literature review payload too large (%d bytes), "
                        "sending bounded payload (%d bytes)",
                        payload_size,
                        fitted_size,
                    )
                    data = fitted_data
            except Exception as exc:
                self.logger.warning(
                    "Could not bound literature review payload: %s",
                    exc,
                )

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
        # ``\u`` is only a valid JSON escape when it is followed by exactly
        # four hexadecimal digits.  Treating every ``\u`` as valid leaves
        # common LaTeX commands such as ``\usepackage`` broken.
        return re.sub(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})', r'\\\\', text)

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
            self.logger.error("Save Error: %s", e)

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
                self.logger.info(
                    "Loaded planner state from DB for session %s "
                    "(hypothesis=%s, plan_steps=%d)",
                    self.session.session_id,
                    self.state.hypothesis.id,
                    len(self.state.plan.steps),
                )
            else:
                self.logger.info(
                    "No persisted planner state found in DB for session %s",
                    self.session.session_id,
                )
        except Exception as e:
            self.logger.error("Load Error: %s", e)
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
                    self.logger.warning(
                        "%s: JSON attempt 1 failed (%s), retrying with escape fix...",
                        log_prefix, e,
                    )
                    continue
                else:
                    self.logger.error(
                        "%s: JSON parse error after escape fix: %s",
                        log_prefix, e,
                    )

                    # FALLBACK: Try YAML parsing for near-JSON content
                    try:
                        self.logger.warning(
                            "%s: Attempting yaml.safe_load as fallback...",
                            log_prefix,
                        )
                        data = yaml.safe_load(content)
                        if data and isinstance(data, dict):
                            self.logger.info(
                                "%s: Successfully parsed using yaml fallback",
                                log_prefix,
                            )
                            return data
                    except Exception as yaml_e:
                        self.logger.error(
                            "%s: YAML fallback also failed: %s",
                            log_prefix, yaml_e,
                        )

                    preview = clean_content[:300].encode(
                        "ascii", errors="replace"
                    ).decode("ascii")
                    self.logger.error(
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
        self.logger.info("[HypothesisArchitect]: %s...", response_content[:100])

        data = await self._parse_llm_json(
            response_content,
            log_prefix="[HypothesisArchitect]",
        )

        match data:
            case None:
                self.logger.error(
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

    async def _process_pipeline_response(self, response_content: str) -> bool:
        """
        Parse pipeline plan JSON from the PipelineArchitect and update state/UI.

        Extracts steps with instructions, persists the plan to the database,
        and emits structured plan updates to the UI canvas.

        Args:
            response_content: Raw response text from the PipelineArchitect.
        """
        self.logger.info("[PipelineArchitect]: %s...", response_content[:100])

        data = await self._parse_llm_json(
            response_content,
            log_prefix="[PipelineArchitect]",
        )

        if not isinstance(data, dict):
            self.logger.error(
                "Failed to parse PipelineArchitect response as JSON. "
                "Sending error message to chat."
            )
            if self.state.plan.steps:
                await self._send(
                    "msg",
                    "The requested revision was not valid, so the last valid "
                    "pipeline plan has been kept unchanged.",
                )
                return True
            await self._send(
                "msg",
                "The agent generated an improperly formatted plan. "
                "Please ask it to regenerate or revise your request.",
            )
            await self._send(
                "planning_failed",
                {"reason": "invalid_pipeline_response"},
            )
            return False

        if "steps" not in data and self.state.plan.steps:
            if data.get("message"):
                await self._send("msg", data["message"])
            await self._save_state()
            return True

        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            self.logger.error(
                "PipelineArchitect response did not contain a non-empty steps list"
            )
            if self.state.plan.steps:
                await self._send(
                    "msg",
                    "The requested revision contained no executable steps, so "
                    "the last valid pipeline plan has been kept unchanged.",
                )
                return True
            await self._send(
                "msg",
                "The generated plan did not contain any executable pipeline steps. "
                "Please ask the agent to regenerate or revise the plan.",
            )
            await self._send(
                "planning_failed",
                {"reason": "empty_pipeline"},
            )
            return False

        if isinstance(steps, list):
            persisted_plan = await self.session_service.handle_plan_update(
                self.session, steps
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
                    for s in steps
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
                for s in steps
            }

        # Forward any accompanying message to chat
        if "message" in data and data["message"]:
            await self._send("msg", data["message"])

        await self._save_state()
        return bool(self.state.plan.steps)

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
        self.logger.info("Building knowledge base with PICO: %s", pico)

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
                    cache_dir=workspace_subpath(self.workspace_dir, ".knowledge_cache"),
                ),
            )
        )

        self.logger.info(
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
        lit_context_path = workspace_subpath(self.workspace_dir, "literature_context.json")

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
            self.logger.info("Literature context saved to %s", lit_context_path)
        except Exception as e:
            self.logger.error("Failed to save literature context: %s", e)

    # ══════════════════════════════════════════════════════════════════════
    # RETRY LOGIC FOR LLM CALLS
    # ══════════════════════════════════════════════════════════════════════

    async def _call_agent_with_retry(
        self,
        agent,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        max_retries: int = 3,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> Tuple[Any, List[Dict[str, str]]]:
        """
        Call an agent with exponential backoff retry logic,
        maintaining a conversation history.

        Returns:
            (response, updated_history)
        """
        # Build the full message list from history
        messages = []
        if history:
            for entry in history:
                messages.append(TextMessage(content=entry["content"], source=entry["role"]))
        # Add the new user prompt
        messages.append(TextMessage(content=prompt, source="user"))

        last_exception = None
        for attempt in range(max_retries):
            try:
                response = await agent.on_messages(messages, cancellation_token=cancellation_token)
                # Append the assistant's response to history
                new_history = (history or []).copy()
                new_history.append({"role": "user", "content": prompt})
                new_history.append({"role": "assistant", "content": response.chat_message.content})
                return response, new_history
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    self.logger.warning("Agent call failed (attempt %d/%d): %s. Retrying in %ds...",
                                        attempt+1, max_retries, e, wait_time)
                    await asyncio.sleep(wait_time)
                else:
                    self.logger.error("Agent call failed after %d attempts: %s", max_retries, e)
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
               plan using the PipelineArchitect agent and accepts ongoing
               chat-based revisions while the planning task remains active.

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
            self.logger.info(
                "Planner %s state before run: %s",
                start_query, self.state,
            )
            self._pipeline_instructions = {}

            # Exit early if hypothesis is already confirmed
            if self.state.hypothesis.state == "confirmed":
                self.logger.info(
                    "Hypothesis already confirmed for session %s, skipping planning",
                    self.session.session_id,
                )
                return

            self.logger.info(
                "State not confirmed, planning phases engaged (session_id=%s)",
                self.session.session_id,
            )

            # ══════════════════════════════════════════════════════════════════
            # PHASE 1: HYPOTHESIS STRUCTURING (CONVERSATIONAL)
            # ══════════════════════════════════════════════════════════════════
            hypothesis_architect = self.agents["hypothesis_architect"]
            hypothesis_confirmed = False
            chat_history = self.state.chat_history.copy()  # load existing history
            locked_roi = {"scope": "both", "labels": [], "rationale": "conversational flow"} # default since validator is removed

            while not hypothesis_confirmed:
                user_input = None
                
                # Use start_query if provided, but ignore generic "Start" on resumed sessions
                if start_query:
                    if start_query == "Start" and chat_history:
                        start_query = None  # ignore
                    else:
                        user_input = start_query
                        start_query = None
                
                # If no input yet, wait for user
                if not user_input:
                    if not chat_history:
                        await self._send("msg", "Please provide your research idea or answer the clarification questions.")
                    user_input = await self.input_provider()

                # Send to architect, passing the full history
                try:
                    response, updated_history = await self._call_agent_with_retry(
                        hypothesis_architect,
                        user_input,
                        history=chat_history,
                        cancellation_token=cancellation_token,
                    )
                    chat_history = updated_history
                except Exception as e:
                    self.logger.error("HypothesisArchitect call failed: %s", e)
                    await self._send("msg", "Sorry, I encountered an error. Please try again or rephrase.")
                    continue

                content = response.chat_message.content
                data = await self._parse_llm_json(content)

                hypothesis_dict = data.get("hypothesis", {})
                if data and isinstance(hypothesis_dict, dict) and hypothesis_dict.get("population") and hypothesis_dict.get("intervention"):
                    # Architect produced a structured hypothesis. Delegate state and DB sync:
                    await self._process_hypothesis_response(content)
                    
                    # AUTO-CONFIRM THE HYPOTHESIS
                    if self.state.hypothesis:
                        self.state.hypothesis.state = "confirmed"
                    if self.session.hypothesis:
                        self.session.hypothesis.state = "confirmed"
                        
                    await self.session_service.confirm_hypothesis(self.session)
                    
                    hypothesis_confirmed = True
                    
                    # Update the in-memory history with auto-confirmation
                    chat_history.append({"role": "assistant", "content": "Hypothesis automatically confirmed."})
                    self.state.chat_history = chat_history
                    await self._save_state()
                    break
                else:
                    # Architect is asking clarifying questions (or parsing failed)
                    await self._process_hypothesis_response(content)
                    self.state.chat_history = chat_history
                    await self._save_state()
                    # The loop continues to get user input

            # After confirmation, save the final history
            self.state.chat_history = chat_history
            await self._save_state()
            
            # Prepare the hypothesis dump for downstream phases
            hypothesis_dump = self.state.hypothesis.model_dump()

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
                    
                    # Generate a clean Markdown version for the UI and the report file
                    clean_ui_knowledge = self._clean_knowledge_for_ui(knowledge_base)
                    clean_ui_sources = self._clean_knowledge_for_ui(sources_guide)


                    # Run multi-agent literature review pipeline
                    workspace_dir = self.workspace_dir
                    lit_output_dir = workspace_subpath(workspace_dir, "literature_review_output")

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
                        cache_dir=workspace_subpath(workspace_dir, ".knowledge_cache"),
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
                        "ui_knowledge": clean_ui_knowledge,
                        "ui_sources": clean_ui_sources,
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
                    self.logger.error(
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
                "LOCKED ROI INTERPRETATION (authoritative):\n"
                f"{json.dumps(locked_roi, ensure_ascii=False, indent=2)}\n\n"
                f"SUMMARIZED DOMAIN KNOWLEDGE (from literature review):\n"
                f"{kb_summary}\n\n"
                "Use the confirmed hypothesis as the primary source of study intent. "
                "Do not ask for missing hypothesis details unless the hypothesis "
                "JSON is empty.\n\n"
                "Based on this hypothesis and the domain knowledge, design an optimal "
                "research pipeline. Select the agents needed, order them logically, and "
                "provide detailed, study-specific instructions for each agent. "
                "Return JSON with 'steps', 'rationale', and 'message'. The JSON "
                "must be strictly valid: escape every backslash inside strings "
                "and prefer plain-language instructions over raw LaTeX commands."
            )

            response, _ = await self._call_agent_with_retry(
                pipeline_architect,
                pipeline_prompt,
                cancellation_token=cancellation_token,
            )
            self.logger.info(
                "PipelineArchitect response: %s",
                response.chat_message.content[:200],
            )
            pipeline_ready = await self._process_pipeline_response(
                response.chat_message.content
            )

            # ── Pipeline Revision Loop (Infinite) ──────────
            while True:
                # ══════════════════════════════════════════════════════════════
                # FINALIZE: Compile task messages with context augmentation
                # ══════════════════════════════════════════════════════════════
                if pipeline_ready and self.state.plan.steps:
                    try:
                        task_messages = compile_task_messages(
                            hypothesis=self.state.hypothesis,
                            plan=self.state.plan,
                            knowledge_sections=knowledge_sections,
                            sources_guide=sources_guide,
                            pipeline_instructions=self._pipeline_instructions,
                            locked_roi_interpretation=locked_roi,
                        )
                        if not task_messages:
                            raise ValueError(
                                "Task message compiler produced an empty execution pipeline"
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
                        pipeline_ready = False
                        self.logger.error(
                            "Task message compilation failed; pipeline is not executable: %s",
                            e,
                            exc_info=True,
                        )
                        await self._send(
                            "msg",
                            "The plan could not be prepared for execution. Please ask "
                            "the agent to regenerate or revise the plan.",
                        )
                        await self._send(
                            "planning_failed",
                            {"reason": "task_compilation_failed"},
                        )

                await self._save_state()
                if pipeline_ready:
                    await self._send(
                        "planning_complete",
                        {
                            "has_pipeline": True,
                            "step_count": len(self.state.plan.steps),
                        },
                    )
                    await self._send("modules_matched", True)
                
                try:
                    # Wait indefinitely for chat-based revisions
                    user_feedback = await self.input_provider()
                except Exception as e:
                    self.logger.info("Input provider stopped, ending planner task: %s", e)
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
                    response, _ = await self._call_agent_with_retry(
                        pipeline_architect,
                        refinement_prompt,
                        cancellation_token=cancellation_token,
                    )
                    pipeline_ready = await self._process_pipeline_response(
                        response.chat_message.content
                    )
                except Exception as e:
                    self.logger.error("Error generating revised pipeline: %s", e)
                    if pipeline_ready and self.state.plan.steps:
                        await self._send(
                            "msg",
                            "The pipeline revision could not be generated, so "
                            "the last valid plan has been kept unchanged.",
                        )
                    else:
                        await self._send(
                            "msg",
                            "The pipeline could not be generated. Please retry "
                            "or revise the request.",
                        )
                        await self._send(
                            "planning_failed",
                            {"reason": "pipeline_revision_failed"},
                        )
                    continue  # Try again on next input
