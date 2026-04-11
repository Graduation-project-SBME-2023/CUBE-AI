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
            comparison=data["comparison"],
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
                logger.info(e)
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
    async def create_plan(cls, plan: StudyPlan) -> StudyPlan | None:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{BASE_URL}/plans/with_steps",
                    params={"hypothesis_id": plan.hypothesis_id},
                    json=plan.model_dump(),
                )
                print(response.status_code)
                print(response.text)
                print(response.json())
                response.raise_for_status()
                return StudyPlan(**response.json())
            except httpx.HTTPError as e:
                logger.error(f"Failed to create plan for hypothesis {plan.hypothesis_id}: {e}")
                return None

    @classmethod
    async def update_plan(cls, plan: StudyPlan) -> StudyPlan | None:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(
                    f"{BASE_URL}/plans/{plan.id}/with_steps",
                    json=plan.model_dump(),
                )
                response.raise_for_status()
                return StudyPlan(**response.json())
            except httpx.HTTPError as e:
                logger.error(f"Failed to update plan with id {plan.id}: {e}")
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
