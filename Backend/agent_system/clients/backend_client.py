import httpx
import logging
from models.plans import ClinicalHypothesis, StudyPlan
import os
import json
from uuid import UUID

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("MONAI_URL","http://localhost:8000")


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
                logger.error(f"Failed to create hypothesis for session {session_id}: {e}")
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
                logger.error(f"Failed to fetch hypothesis for session {session_id}: {e}")
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
                logger.error(f"Failed to create plan for hypothesis {plan.hypothesis_id}: {e}")
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
                logger.error(f"Failed to fetch plan for hypothesis {hypothesis_id}: {e}")
                return None


if __name__ == '__main__':
    async def hypothesis_test():
        from models.plans import ClinicalHypothesis

        client = BackendClient()
        hypothesis = ClinicalHypothesis(
            title='Hypothesis Title',
            population='Population',
            intervention='Intervention',
            outcome='Outcome',
            clinical_context='Clinical Context',
            relationship_type='Relationship Type',
            comparison='Comparison',
            question='Question',
            state='State',
        )

        hypothesis = await client.create_hypothesis('cf8b8a59-317b-4d6c-960e-dd7e60b81bd2', hypothesis)
        print(hypothesis)

    async def plan_test():
        import time
        from models.plans import StudyStep
        client = BackendClient()
        plan = await client.create_plan(
            plan=StudyPlan(
                hypothesis_id='21d5b06c-958d-4d78-b799-5ee3e6848f57',
                study_goal='Study Goal',
                steps=[
                    StudyStep(
                        title='Study Step 1',
                        description='Study Step 1',
                        module='Study Step 1',
                    ),
                    StudyStep(
                        title='Study Step 2',
                        description='Study Step 2',
                        module='Study Step 2',
                    )
                ]
            )
        )
        print(plan)

        time.sleep(20)

        plan.state = 'confirmed'

        updated_plan = await client.update_plan(plan=plan)
        print(updated_plan)

    import asyncio
    asyncio.run(hypothesis_test())
