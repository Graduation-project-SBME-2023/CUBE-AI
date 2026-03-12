import httpx
import logging
from models.plans import ClinicalHypothesis, StudyPlan

logger = logging.getLogger(__name__)

BASE_URL = "http://localhost:8000"


class BackendClient:
    """
    Async HTTP client for the backend REST API.
    All methods are async — uses httpx instead of requests
    to avoid blocking the event loop.
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _parse_hypothesis(cls, data: dict) -> ClinicalHypothesis:
        return ClinicalHypothesis(
            id=data["id"],
            state=data["state"],
            title=data["title"],
            population=data["population"],
            intervention=data["intervention"],
            outcome=data["outcome"],
            clinical_context=data["clinical_context"],
            relationship_type=data["relationship_type"],
        )

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    @classmethod
    async def update_session(cls, session_id: str, data: dict) -> dict | None:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(
                    f"{BASE_URL}/sessions/{session_id}",
                    json=data,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Failed to update session {session_id}: {e}")
                return None

    # ------------------------------------------------------------------
    # Hypothesis
    # ------------------------------------------------------------------

    @classmethod
    async def create_hypothesis(
        cls, session_id: str, hypothesis: ClinicalHypothesis
    ) -> ClinicalHypothesis | None:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{BASE_URL}/hypotheses/",
                    params={"chat_id": session_id},
                    json=hypothesis.model_dump(),
                )
                response.raise_for_status()
                return cls._parse_hypothesis(response.json())
            except httpx.HTTPError as e:
                logger.error(f"Failed to create hypothesis for session {session_id}: {e}")
                return None

    @classmethod
    async def update_hypothesis(
        cls, hypothesis: ClinicalHypothesis
    ) -> ClinicalHypothesis | None:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(
                    f"{BASE_URL}/hypotheses/{hypothesis.id}",
                    json=hypothesis.model_dump(),
                )
                response.raise_for_status()
                return cls._parse_hypothesis(response.json())
            except httpx.HTTPError as e:
                logger.error(f"Failed to update hypothesis {hypothesis.id}: {e}")
                return None

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    @classmethod
    async def create_plan(cls, session_id: str, plan: StudyPlan) -> StudyPlan | None:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{BASE_URL}/plans/",
                    params={"chat_id": session_id},
                    json=plan.model_dump(),
                )
                response.raise_for_status()
                return StudyPlan(**response.json())
            except httpx.HTTPError as e:
                logger.error(f"Failed to create plan for session {session_id}: {e}")
                return None

    @classmethod
    async def update_plan(cls, session_id: str, plan: StudyPlan) -> StudyPlan | None:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(
                    f"{BASE_URL}/plans/{session_id}",
                    json=plan.model_dump(),
                )
                response.raise_for_status()
                return StudyPlan(**response.json())
            except httpx.HTTPError as e:
                logger.error(f"Failed to update plan for session {session_id}: {e}")
                return None
