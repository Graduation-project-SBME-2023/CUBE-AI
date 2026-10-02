import httpx
import logging
from datetime import datetime
from models.plans import (
    ClinicalHypothesis,
    ContextAugmentation,
    StudyPlan,
    StudyStep,
    TaskMessage,
)
from models.logs import SessionEvent
import os
import json
from uuid import UUID
from typing import Any
from services.timeout_policy import CONTROL_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get(
    "APP_API_URL",
    os.environ.get("CONTROL_API_URL", "http://localhost:8001"),
).rstrip("/")
DEFAULT_TIMEOUT = CONTROL_HTTP_TIMEOUT
class BackendClient:
    """Async HTTP client for the backend REST API. All methods are async — uses httpx instead of requests to avoid blocking the event loop."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_valid_uuid(value: str | None) -> bool:
        if not value:
            return False
        try:
            UUID(str(value))
            return True
        except (ValueError, TypeError, AttributeError):
            return False

    @staticmethod
    def _stringify(value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    @classmethod
    def _build_intent_payload(cls, hypothesis: ClinicalHypothesis) -> dict:
        """Versioned JSONB for sessions.intent. Never a copy of the prompt."""
        return {
            "schema_version": 1,
            "source": "planning_agent",
            "title": hypothesis.title or "Untitled Hypothesis",
            "population": cls._stringify(hypothesis.population),
            "intervention": cls._stringify(hypothesis.intervention),
            "comparison": cls._stringify(hypothesis.comparison),
            "outcome": cls._stringify(hypothesis.outcome),
            "statement": cls._stringify(hypothesis.hypothesis),
            "clinical_context": cls._stringify(hypothesis.clinical_context),
            "relationship_type": cls._stringify(hypothesis.relationship_type),
        }

    @classmethod
    def _parse_intent(cls, session: dict) -> ClinicalHypothesis:
        """Rebuild the in-memory hypothesis from a session row."""
        intent = session.get("intent") or {}
        return ClinicalHypothesis(
            id=str(session.get("session_id", "")),
            state="not_confirmed"
            if session.get("control_state") == "planning"
            else "confirmed",
            title=intent.get("title") or session.get("title") or "",
            population=intent.get("population") or "",
            intervention=intent.get("intervention") or "",
            outcome=intent.get("outcome") or "",
            clinical_context=intent.get("clinical_context") or "",
            relationship_type=intent.get("relationship_type") or "",
            comparison=intent.get("comparison") or "",
            hypothesis=intent.get("statement") or "",
            question=session.get("prompt") or "",
            message=None,
        )

    @staticmethod
    def _build_task_message_payload(task_message: dict[str, Any]) -> dict[str, Any]:
        return {
            "phase": task_message.get("phase", ""),
            "agent_title": task_message.get("agent_title", ""),
            "instructions": task_message.get("instructions", ""),
            "hypothesis_summary": task_message.get("hypothesis_summary", ""),
            "payload": task_message.get("payload", {}) or {},
        }

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    @classmethod
    def _require_uuid(cls, value, what: str) -> str:
        """current rejects non-UUID session ids at every boundary."""
        if not cls._is_valid_uuid(value):
            raise ValueError(
                f"{what} must be a UUID; got {value!r}. V1 does not accept "
                "local-only session identifiers at the API boundary."
            )
        return str(value)

    @classmethod
    async def update_session(cls, session_id: str, data: dict) -> dict | None:
        """PATCH /sessions/{id}."""
        cls._require_uuid(session_id, "session_id")
        payload = {k: v for k, v in data.items() if k != "prompt"}
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.patch(
                    f"{BASE_URL}/sessions/{session_id}",
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Failed to update session {session_id}: {e}")
                return None

    @classmethod
    async def get_session(cls, session_id: str) -> dict | None:
        cls._require_uuid(session_id, "session_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.get(f"{BASE_URL}/sessions/{session_id}")
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Failed to fetch session {session_id}: {e}")
                return None

    @classmethod
    async def set_session_control_state(cls, session_id: str, control_state: str) -> dict | None:
        """Guarded transition — this is how pause becomes durable."""
        cls._require_uuid(session_id, "session_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.patch(
                    f"{BASE_URL}/sessions/{session_id}/control-state/{control_state}"
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to set session {session_id} control state to {control_state}: {e}"
                )
                return None

    # ------------------------------------------------------------------
    # Intent
    # ------------------------------------------------------------------

    @classmethod
    async def save_intent(
        cls, session_id: str, hypothesis: ClinicalHypothesis
    ) -> ClinicalHypothesis | None:
        """Persist the planner's structured reading of the prompt."""
        cls._require_uuid(session_id, "session_id")
        intent = cls._build_intent_payload(hypothesis)
        has_pico = any(
            intent.get(field) for field in ("population", "intervention", "outcome")
        )
        # sessions.title is only ever set at creation (almost always blank —
        # a hypothesis session is created from the raw prompt, not a title),
        # and history/list views read the lightweight SessionSummary schema,
        # which has no `intent` field to fall back to. Mirror the planner's
        # title (never blank — _build_intent_payload defaults it) onto the
        # plain column so those views show it instead of the untruncated raw
        # prompt.
        payload = {
            "intent": intent,
            "prompt_kind": "hypothesis" if has_pico else "general",
            "title": intent["title"],
        }
        updated = await cls.update_session(session_id, payload)
        if updated is None:
            return None
        return cls._parse_intent(updated)

    @classmethod
    async def get_intent(cls, session_id: str) -> ClinicalHypothesis | None:
        session = await cls.get_session(session_id)
        if session is None:
            return None
        return cls._parse_intent(session)

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    @classmethod
    def _nodes_payload(cls, plan: StudyPlan) -> dict:
        """Map the in-memory plan onto the session graph draft contract."""
        nodes = []
        for step in plan.steps:
            params: dict[str, Any] = {}
            if step.description:
                params["description"] = step.description
            if step.task_message is not None:
                task_message = step.task_message
                params["compiled"] = task_message.instructions
                params["task_message"] = {
                    "schema_version": 1,
                    "source": "context_compiler",
                    "task_id": task_message.task_id,
                    "phase": task_message.phase,
                    "agent_title": task_message.agent_title,
                    "instructions": task_message.instructions,
                    "hypothesis_summary": task_message.hypothesis_summary,
                    "payload": task_message.payload or {},
                    "context_augmentation": (
                        task_message.context_augmentation.model_dump()
                        if task_message.context_augmentation is not None
                        else {}
                    ),
                }
            nodes.append(
                {
                    "title": step.title,
                    "target_name": step.module or "",
                    "params": params,
                }
            )
        return {"nodes": nodes}

    @staticmethod
    def _parse_task_message(params: dict) -> TaskMessage | None:
        """Rebuild the compiled task message from the node definition."""
        raw = (params or {}).get("task_message")
        if not isinstance(raw, dict):
            return None
        augmentation = raw.get("context_augmentation") or {}
        return TaskMessage(
            task_id=str(raw.get("task_id") or ""),
            phase=str(raw.get("phase") or ""),
            agent_id=str(raw.get("agent_id") or ""),
            agent_title=str(raw.get("agent_title") or ""),
            instructions=str(raw.get("instructions") or ""),
            hypothesis_summary=str(raw.get("hypothesis_summary") or ""),
            payload=raw.get("payload") or {},
            context_augmentation=ContextAugmentation(
                **{
                    field: str(augmentation.get(field) or "")
                    for field in ContextAugmentation.model_fields
                }
            ),
        )

    @classmethod
    def _parse_plan(cls, session_id: str, payload: dict) -> StudyPlan:
        steps = []
        for raw in sorted(
            payload.get("nodes") or [], key=lambda r: r.get("position", 0)
        ):
            params = raw.get("params") or {}
            task_message = cls._parse_task_message(params)
            if task_message is not None and not task_message.agent_id:
                # The agent id is the node's own selector; it is not stored
                # twice inside the task message.
                task_message.agent_id = raw.get("target_name") or ""
            steps.append(
                StudyStep(
                    id=str(raw.get("node_id", "")),
                    title=raw.get("title") or "",
                    description=params.get("description", ""),
                    module=raw.get("target_name"),
                    position=int(raw.get("position") or 0),
                    task_message=task_message,
                )
            )
        return StudyPlan(
            id=str(payload.get("session_id", session_id)),
            session_id=str(payload.get("session_id", session_id)),
            steps=steps,
            state="confirmed" if payload.get("approved_at") else "not_confirmed",
        )

    @classmethod
    async def save_plan_draft(cls, session_id: str, plan: StudyPlan) -> StudyPlan | None:
        """Replace the session's draft step list."""
        cls._require_uuid(session_id, "session_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.put(
                    f"{BASE_URL}/session-graphs/session/{session_id}/nodes",
                    json=cls._nodes_payload(plan),
                )
                response.raise_for_status()
                return cls._parse_plan(session_id, response.json())
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to save plan draft for session {session_id}: "
                    f"{type(e).__name__}: {e}"
                )
                return None

    @classmethod
    async def approve_plan(cls, session_id: str) -> StudyPlan | None:
        """One-way approval. A second call is a 409, never a silent no-op."""
        cls._require_uuid(session_id, "session_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{BASE_URL}/session-graphs/session/{session_id}/approve"
                )
                response.raise_for_status()
                return cls._parse_plan(session_id, response.json())
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to approve plan for session {session_id}: "
                    f"{type(e).__name__}: {e}"
                )
                return None

    @classmethod
    async def get_plan_by_session(cls, session_id: str) -> StudyPlan | None:
        cls._require_uuid(session_id, "session_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.get(f"{BASE_URL}/session-graphs/session/{session_id}")
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return cls._parse_plan(session_id, response.json())
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to fetch plan for session {session_id}: "
                    f"{type(e).__name__}: {e}"
                )
                return None

    # ------------------------------------------------------------------
    # Plan Steps
    # ------------------------------------------------------------------

    @classmethod
    async def get_graph_nodes(cls, session_id: str) -> list[dict]:
        """Fetch a session's steps in order."""
        cls._require_uuid(session_id, "session_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.get(f"{BASE_URL}/graph-nodes/session/{session_id}")
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to fetch steps for session {session_id}: "
                    f"{type(e).__name__}: {e}"
                )
                return []

    # ------------------------------------------------------------------
    # Segmentations
    # ------------------------------------------------------------------

    @classmethod
    async def get_segmentations_by_volume(cls, volume_uid: str) -> list[dict]:
        """Every registered DICOM SEG for one source volume.

        This is what makes a mask addressable by what it *is* -- a segmentation
        of a volume -- instead of by a filename convention in a workspace.
        """
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.get(
                    f"{BASE_URL}/segmentation-masks/by-volume/{volume_uid}",
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to fetch segmentations for volume {volume_uid}: "
                    f"{type(e).__name__}: {e}"
                )
                return []

    # ------------------------------------------------------------------
    # Node runs — execution state, identity
    # ------------------------------------------------------------------

    @classmethod
    async def get_node_runs(cls, session_id: str) -> list[dict]:
        """Every run of the session's approved graph, in node order."""
        cls._require_uuid(session_id, "session_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.get(
                    f"{BASE_URL}/runs/session/{session_id}"
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to fetch agent runs for session {session_id}: "
                    f"{type(e).__name__}: {e}"
                )
                return []

    @classmethod
    async def get_node_run(cls, run_id: str) -> dict | None:
        cls._require_uuid(run_id, "run_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(f"{BASE_URL}/runs/{run_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    # ------------------------------------------------------------------
    # Task Messages + Context Augmentation
    # ------------------------------------------------------------------

    @classmethod
    async def get_node_status(cls, session_id: str) -> dict | None:
        """The `node_status` view, rebuilt from the runs."""
        cls._require_uuid(session_id, "session_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.get(
                    f"{BASE_URL}/runs/session/{session_id}/status"
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to fetch run status for session {session_id}: "
                    f"{type(e).__name__}: {e}"
                )
                return None

    @classmethod
    async def start_node_run(
        cls, run_id: str, compiled_prompt: str, resolved_inputs: dict[str, Any]
    ) -> dict | None:
        """Claim a run immediately before its step starts."""
        cls._require_uuid(run_id, "run_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(
                f"{BASE_URL}/runs/{run_id}/start",
                json={"compiled_prompt": compiled_prompt, "resolved_inputs": resolved_inputs},
            )
            response.raise_for_status()
            return response.json()

    @classmethod
    async def complete_node_run(
        cls, run_id: str, scratchpad_summary: dict[str, Any] | None = None
    ) -> dict | None:
        cls._require_uuid(run_id, "run_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{BASE_URL}/runs/{run_id}/complete",
                    json={"scratchpad_summary": scratchpad_summary},
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to complete agent run {run_id}: "
                    f"{type(e).__name__}: {e}"
                )
                return None

    @classmethod
    async def write_agent_output(
        cls, run_id: str, output: dict[str, Any]
    ) -> dict | None:
        """Record a node's structured result on its run."""
        cls._require_uuid(run_id, "run_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.put(
                f"{BASE_URL}/runs/{run_id}/output", json=output
            )
            response.raise_for_status()
            return response.json()

    @classmethod
    async def fail_node_run(
        cls,
        run_id: str,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict | None:
        cls._require_uuid(run_id, "run_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{BASE_URL}/runs/{run_id}/fail",
                    json={
                        "code": code,
                        "message": message[:8000],
                        "details": details,
                    },
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to record failure for agent run {run_id}: "
                    f"{type(e).__name__}: {e}"
                )
                return None

    # ------------------------------------------------------------------
    # Population and segmentation review
    # ------------------------------------------------------------------
    # Unlike the best-effort readers above, these raise on transport failure:
    # a cohort or review that cannot be read must stop the caller, never look
    # like "nothing recorded yet".

    @classmethod
    async def _domain(cls, method: str, path: str, *, allow_404: bool = False, **kwargs):
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.request(method, f"{BASE_URL}{path}", **kwargs)
        if allow_404 and response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json() if response.content else None

    @classmethod
    async def get_population_cohort(cls, session_id: str) -> dict | None:
        cls._require_uuid(session_id, "session_id")
        return await cls._domain(
            "GET", f"/cohorts/session/{session_id}/population", allow_404=True
        )

    @classmethod
    async def get_cohort_analysis_rows(cls, cohort_id: str) -> list[dict]:
        cls._require_uuid(cohort_id, "cohort_id")
        return await cls._domain("GET", f"/cohorts/{cohort_id}/analysis-rows")

    @classmethod
    async def get_segmentation_batches(cls, session_id: str) -> list[dict]:
        cls._require_uuid(session_id, "session_id")
        return await cls._domain("GET", f"/segmentation-batches/session/{session_id}")

    # ------------------------------------------------------------------
    # Literature reviews — the planning-phase literature agent's durable output
    # ------------------------------------------------------------------

    @classmethod
    async def create_literature_review(
        cls,
        session_id: str,
        review_key: str,
        query: dict,
        result_summary: dict | None = None,
    ) -> dict:
        """Insert-or-read a literature review for this session/PICO."""
        cls._require_uuid(session_id, "session_id")
        return await cls._domain(
            "POST",
            "/literature-reviews/",
            json={
                "session_id": session_id,
                "review_key": review_key,
                "query": query,
                "result_summary": result_summary,
            },
        )

    @classmethod
    async def add_literature_papers(cls, review_id: str, papers: list[dict]) -> dict:
        """Batch-add papers to a review, deduplicating by PMID and DOI."""
        cls._require_uuid(review_id, "review_id")
        return await cls._domain(
            "POST", f"/literature-reviews/{review_id}/papers", json=papers
        )

    @classmethod
    async def create_report(
        cls,
        session_id: str,
        source_text: str,
        *,
        created_by_run_id: str | None = None,
        literature_review_id: str | None = None,
        source_format: str = "latex",
        title: str | None = None,
    ) -> dict:
        """Insert-or-read a report by its natural key (agent run or
        literature review — exactly one of the two, R13)."""
        cls._require_uuid(session_id, "session_id")
        payload: dict[str, Any] = {
            "session_id": session_id,
            "source_text": source_text,
            "source_format": source_format,
        }
        if created_by_run_id is not None:
            payload["created_by_run_id"] = created_by_run_id
        if literature_review_id is not None:
            payload["literature_review_id"] = literature_review_id
        if title is not None:
            payload["title"] = title
        return await cls._domain("POST", "/reports/", json=payload)

    # ------------------------------------------------------------------
    # Hypothesis History
    # ------------------------------------------------------------------

    @classmethod
    async def get_hypothesis_history(cls, limit: int = 20) -> list[dict]:
        """Recent research sessions, most recently updated first."""
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.get(
                    f"{BASE_URL}/sessions/",
                    params={"include_archived": True, "limit": min(limit, 100)},
                )
                response.raise_for_status()
                sessions = response.json()
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to fetch session history: {type(e).__name__}: {e}"
                )
                return []

        history = []
        for session in sessions[:limit]:
            intent = session.get("intent") or {}
            history.append(
                {
                    "id": str(session.get("session_id")),
                    "title": intent.get("title")
                    or session.get("title")
                    or (session.get("prompt") or "Untitled")[:80],
                    "prompt": session.get("prompt"),
                    "prompt_kind": session.get("prompt_kind"),
                    "date": session.get("updated_at") or session.get("created_at"),
                    "status": session.get("control_state"),
                }
            )
        logger.info(f"Fetched session history: {len(history)} items")
        return history

    # ------------------------------------------------------------------
    # Session events
    # ------------------------------------------------------------------
    @classmethod
    async def save_event(cls, log: SessionEvent) -> SessionEvent:
        """Persist one event and return it carrying its `event_id`."""
        cls._require_uuid(log.session_id, "log.session_id")

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(
                f"{BASE_URL}/session-events/",
                json={
                    "session_id": log.session_id,
                    "event_type": log.event_type,
                    "payload": log.payload,
                },
            )
            response.raise_for_status()
            return SessionEvent(**response.json())

    @classmethod
    async def replay_events(
        cls, session_id: str, after_event_id: int = 0, limit: int = 2000
    ) -> list[SessionEvent]:
        """Replay a session's events in `event_id` order from a cursor.

        Raises on failure rather than swallowing it (WEBSOCKET_FIX_PLAN.md
        W4): an empty return used to be ambiguous between "nothing new" and
        "the backend is unreachable," so a failed replay looked like a
        successful, empty one. The handshake-cursor replay loop
        (socket_store.ConnectionManager.replay, WS-08) depends on being able
        to tell the two apart -- it sends an error frame and closes the
        socket on a genuine failure, never a silent empty session.
        """
        cls._require_uuid(session_id, "session_id")
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                f"{BASE_URL}/session-events/session/{session_id}",
                params={"after_event_id": after_event_id, "limit": limit},
            )
            response.raise_for_status()
            return [SessionEvent(**item) for item in response.json()]

    @classmethod
    async def get_logs(cls, session_id: str) -> list[SessionEvent]:
        """Every event of a session, oldest first (replay from the beginning)."""
        return await cls.replay_events(session_id, after_event_id=0)


if __name__ == "__main__":

    async def intent_test():
        from models.plans import ClinicalHypothesis

        client = BackendClient()
        hypothesis = ClinicalHypothesis(
            title="Hypothesis Title",
            population="Population",
            intervention="Intervention",
            outcome="Outcome",
            clinical_context="Clinical Context",
            relationship_type="Relationship Type",
            comparison="Comparison",
            question="Question",
            state="State",
        )
        print(
            await client.save_intent(
                "cf8b8a59-317b-4d6c-960e-dd7e60b81bd2", hypothesis
            )
        )

    import asyncio

    asyncio.run(intent_test())
