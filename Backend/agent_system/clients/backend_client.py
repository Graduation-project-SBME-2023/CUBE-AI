import httpx
import logging
from datetime import datetime
from models.plans import ClinicalHypothesis, StudyPlan, StudyStep
from models.logs import WS_Log
import os
import json
from uuid import UUID
from typing import Any

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("MONAI_URL", "http://localhost:8000")


class BackendClient:
    """
    Async HTTP client for the backend REST API.
    All methods are async — uses httpx instead of requests
    to avoid blocking the event loop.
    """

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
    def _build_hypothesis_payload(cls, hypothesis: ClinicalHypothesis) -> dict:
        # Match monailabel.schemas.hypothesis.HypothesisCreate exactly.
        return {
            "title": hypothesis.title or "Untitled Hypothesis",
            "population": cls._stringify(hypothesis.population),
            "intervention": cls._stringify(hypothesis.intervention),
            "comparison": cls._stringify(hypothesis.comparison),
            "outcome": cls._stringify(hypothesis.outcome),
            "hypothesis": cls._stringify(hypothesis.hypothesis),
            "clinical_context": cls._stringify(hypothesis.clinical_context),
            "relationship_type": cls._stringify(hypothesis.relationship_type),
            "state": cls._stringify(hypothesis.state) or "not_confirmed",
        }

    @classmethod
    def _parse_hypothesis(cls, data: dict) -> ClinicalHypothesis:
        return ClinicalHypothesis(
            id=str(data.get("id", "")),
            state=data.get("state", "not_confirmed"),
            title=data.get("title", ""),
            population=data.get("population", ""),
            intervention=data.get("intervention", ""),
            outcome=data.get("outcome", ""),
            clinical_context=data.get("clinical_context", ""),
            relationship_type=data.get("relationship_type", ""),
            comparison=data.get("comparison", ""),
            hypothesis=data.get("hypothesis", ""),
            # These fields are not persisted in monailabel hypothesis schema.
            question="",
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
    async def update_session(cls, session_id: str, data: dict) -> dict | None:
        pass
        # Session management not yet implemented in DB
        # async with httpx.AsyncClient() as client:
        #     try:
        #         response = await client.patch(
        #             f"{BASE_URL}/sessions/{session_id}",
        #             json=data,
        #         )
        #         response.raise_for_status()
        #         return response.json()
        #     except httpx.HTTPError as e:
        #         logger.error(f"Failed to update session {session_id}: {e}")
        #         return None

    # ------------------------------------------------------------------
    # Hypothesis
    # ------------------------------------------------------------------

    @classmethod
    async def create_hypothesis(
        cls, session_id: str, hypothesis: ClinicalHypothesis
    ) -> ClinicalHypothesis | None:
        if not cls._is_valid_uuid(session_id):
            logger.warning(
                "Skipping hypothesis persistence: session_id is not a UUID (%r)",
                session_id,
            )
            return None

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{BASE_URL}/hypotheses/",
                    params={"chat_id": session_id},
                    json=cls._build_hypothesis_payload(hypothesis),
                )
                response.raise_for_status()
                return cls._parse_hypothesis(response.json())
            except httpx.HTTPError as e:
                logger.info(e)
                logger.error(
                    f"Failed to create hypothesis for session {session_id}: {e}"
                )
                return None

    @classmethod
    async def update_hypothesis(
        cls, hypothesis: ClinicalHypothesis
    ) -> ClinicalHypothesis | None:
        if not cls._is_valid_uuid(hypothesis.id):
            logger.warning(
                "Skipping hypothesis update: hypothesis.id is not a UUID (%r)",
                hypothesis.id,
            )
            return None

        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(
                    f"{BASE_URL}/hypotheses/{hypothesis.id}",
                    json=cls._build_hypothesis_payload(hypothesis),
                )
                response.raise_for_status()
                return cls._parse_hypothesis(response.json())
            except httpx.HTTPError as e:
                logger.error(f"Failed to update hypothesis {hypothesis.id}: {e}")
                return None

    @classmethod
    async def get_hypothesis_by_chat(cls, session_id: str) -> ClinicalHypothesis | None:
        if not cls._is_valid_uuid(session_id):
            logger.warning(
                "Skipping hypothesis fetch: session_id is not a UUID (%r)",
                session_id,
            )
            return None

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{BASE_URL}/hypotheses/by-chat/{session_id}",
                )
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return cls._parse_hypothesis(response.json())
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to fetch hypothesis for session {session_id}: {e}"
                )
                return None

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    @classmethod
    async def create_plan(cls, plan: StudyPlan) -> StudyPlan | None:
        if not cls._is_valid_uuid(plan.hypothesis_id):
            logger.warning(
                "Skipping plan persistence: hypothesis_id is not a UUID (%r)",
                plan.hypothesis_id,
            )
            return None

        payload = {
            "state": plan.state,
            "steps": [
                {
                    "title": step.title,
                    "description": step.description,
                    "module": step.module,
                    "status": step.status,
                    "logs": step.logs,
                    "instructions": {},
                }
                for step in plan.steps
            ],
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{BASE_URL}/plans/with_steps",
                    params={"hypothesis_id": plan.hypothesis_id},
                    json=payload,
                )
                response.raise_for_status()
                return StudyPlan(**response.json())
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to create plan for hypothesis {plan.hypothesis_id}: {e}"
                )
                return None

    @classmethod
    async def update_plan(cls, plan: StudyPlan) -> StudyPlan | None:
        if not cls._is_valid_uuid(plan.id):
            logger.warning(
                "Skipping plan update: plan.id is not a UUID (%r)",
                plan.id,
            )
            return None

        payload = {
            "state": plan.state,
            "steps": [
                {
                    "title": step.title,
                    "description": step.description,
                    "module": step.module,
                    "status": step.status,
                    "logs": step.logs,
                    "instructions": {},
                }
                for step in plan.steps
            ],
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(
                    f"{BASE_URL}/plans/{plan.id}/with_steps",
                    json=payload,
                )
                response.raise_for_status()
                return StudyPlan(**response.json())
            except httpx.HTTPError as e:
                logger.error(f"Failed to update plan with id {plan.id}: {e}")
                return None

    @classmethod
    async def get_plan_by_hypothesis(cls, hypothesis_id: str) -> StudyPlan | None:
        if not cls._is_valid_uuid(hypothesis_id):
            logger.warning(
                "Skipping plan fetch: hypothesis_id is not a UUID (%r)",
                hypothesis_id,
            )
            return None

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{BASE_URL}/plans/by-hypothesis/{hypothesis_id}",
                )
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                payload = response.json()
                if not payload:
                    return None

                if isinstance(payload, list):
                    first_plan = payload[0]
                else:
                    first_plan = payload

                return StudyPlan(**first_plan)
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to fetch plan for hypothesis {hypothesis_id}: {e}"
                )
                return None

    # ------------------------------------------------------------------
    # Plan Steps (execution sync)
    # ------------------------------------------------------------------

    @classmethod
    async def update_plan_step(cls, step_id: str, data: dict) -> dict | None:
        """Patch a single plan step's status and/or logs."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(
                    f"{BASE_URL}/steps/{step_id}/status",
                    json=data,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Failed to update plan step {step_id}: {e}")
                return None

    @classmethod
    async def get_plan_steps(cls, plan_id: str) -> list[dict]:
        """Fetch all steps for a plan — used to recover execution state."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{BASE_URL}/steps/plan/{plan_id}",
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Failed to fetch steps for plan {plan_id}: {e}")
                return []

    # ------------------------------------------------------------------
    # Task Messages + Context Augmentation
    # ------------------------------------------------------------------

    @classmethod
    async def save_task_message_by_step_id(
        cls,
        step_id: str,
        task_message: dict[str, Any],
    ) -> dict | None:
        """Create/update task message for a step_id."""
        if not cls._is_valid_uuid(step_id):
            logger.warning(
                "Skipping task message save: step_id is not a UUID (%r)", step_id
            )
            return None

        payload = cls._build_task_message_payload(task_message)

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{BASE_URL}/task-messages/step/{step_id}",
                    json=payload,
                )
                if response.status_code == 409:
                    response = await client.patch(
                        f"{BASE_URL}/task-messages/step/{step_id}",
                        json=payload,
                    )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Failed to save task message for step {step_id}: {e}")
                return None

    @classmethod
    async def get_task_message_by_step_id(cls, step_id: str) -> dict | None:
        """Read task message by step_id."""
        if not cls._is_valid_uuid(step_id):
            logger.warning(
                "Skipping task message read: step_id is not a UUID (%r)", step_id
            )
            return None

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{BASE_URL}/task-messages/step/{step_id}")
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Failed to read task message for step {step_id}: {e}")
                return None

    @classmethod
    async def save_context_augmentation_by_task_id(
        cls,
        task_id: str,
        context_augmentation: dict[str, Any],
    ) -> dict | None:
        """Update context augmentation by task_id."""
        payload = {
            "knowledge_summary": context_augmentation.get("knowledge_summary", ""),
            "literature_findings": context_augmentation.get("literature_findings", ""),
            "methodology_guidance": context_augmentation.get(
                "methodology_guidance", ""
            ),
            "terminology": context_augmentation.get("terminology", ""),
            "sources_guide": context_augmentation.get("sources_guide", ""),
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(
                    f"{BASE_URL}/context-augmentations/task/{task_id}",
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to save context augmentation for task {task_id}: {e}"
                )
                return None

    @classmethod
    async def get_context_augmentation_by_task_id(cls, task_id: str) -> dict | None:
        """Read context augmentation by task_id."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{BASE_URL}/context-augmentations/task/{task_id}"
                )
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(
                    f"Failed to read context augmentation for task {task_id}: {e}"
                )
                return None

    # ------------------------------------------------------------------
    # Hypothesis History
    # ------------------------------------------------------------------

    @classmethod
    async def get_hypothesis_history(cls, limit: int = 20) -> list[dict]:
        """Fetch hypothesis history: recent chat sessions with their hypotheses."""
        async with httpx.AsyncClient() as client:
            try:
                sessions_resp = await client.get(
                    f"{BASE_URL}/chat_sessions/",
                    params={"active_only": False},
                )
                if sessions_resp.status_code != 200:
                    logger.error(
                        f"Failed to fetch chat sessions: {sessions_resp.status_code}"
                    )
                    return []

                sessions = sessions_resp.json()
                history = []

                def _parse_date(session_data):
                    date_value = session_data.get("last_activity") or session_data.get("started_at")
                    if not date_value:
                        return 0
                    try:
                        return datetime.fromisoformat(date_value).timestamp()
                    except Exception:
                        return 0

                sorted_sessions = sorted(
                    sessions,
                    key=_parse_date,
                    reverse=True,
                )

                for session in sorted_sessions[:limit]:
                    chat_id = session.get("chat_id")
                    if not chat_id:
                        continue

                    title = session.get("title", "Untitled")
                    try:
                        hyp_resp = await client.get(
                            f"{BASE_URL}/hypotheses/by-chat/{chat_id}"
                        )
                        if hyp_resp.status_code == 200:
                            hypothesis = hyp_resp.json()
                            title = hypothesis.get("title", title)
                    except httpx.HTTPError:
                        pass

                    history.append(
                        {
                            "id": str(chat_id),
                            "title": title,
                            "date": session.get("last_activity")
                            or session.get("started_at"),
                            "status": "Completed"
                            if session.get("is_active", True)
                            else "Draft",
                        }
                    )
                logger.info(f"Fetched hypothesis history: {len(history)} items")
                return history
            except httpx.HTTPError as e:
                logger.error(f"Failed to fetch hypothesis history: {e}")
                return []

    # ------------------------------------------------------------------
    # WS Stream Logs
    # ------------------------------------------------------------------
    @classmethod
    async def save_log(cls, log: WS_Log) -> WS_Log:
        """Save a ws log whenever the socket store sends any message."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{BASE_URL}/ws_logs/",
                    json=log.model_dump(),
                )
                response.raise_for_status()
                return WS_Log(**response.json())
            except httpx.HTTPError as e:
                logger.error(f"Failed to save ws log {log.msg_type}: {e}")
                return log

    @classmethod
    async def get_logs(cls, session_id: str) -> list[WS_Log]:
        """Get all logs sent in a session."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{BASE_URL}/ws_logs/session/{session_id}",
                )
                response.raise_for_status()
                data = response.json()
                return [WS_Log(**item) for item in data]
            except httpx.HTTPError as e:
                logger.error(f"Failed to retrieve ws logs for session {session_id}: {e}")
                return []


if __name__ == "__main__":

    async def hypothesis_test():
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

        hypothesis = await client.create_hypothesis(
            "cf8b8a59-317b-4d6c-960e-dd7e60b81bd2", hypothesis
        )
        print(hypothesis)

    async def plan_test():
        import time
        from models.plans import StudyStep

        client = BackendClient()
        plan = await client.create_plan(
            plan=StudyPlan(
                hypothesis_id="21d5b06c-958d-4d78-b799-5ee3e6848f57",
                study_goal="Study Goal",
                steps=[
                    StudyStep(
                        title="Study Step 1",
                        description="Study Step 1",
                        module="Study Step 1",
                    ),
                    StudyStep(
                        title="Study Step 2",
                        description="Study Step 2",
                        module="Study Step 2",
                    ),
                ],
            )
        )
        print(plan)

        time.sleep(20)

        plan.state = "confirmed"

        updated_plan = await client.update_plan(plan=plan)
        print(updated_plan)

    import asyncio

    asyncio.run(hypothesis_test())
