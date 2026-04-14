import logging

from clients.backend_client import BackendClient
from models.plans import PlannerState, StudyPlan

logger = logging.getLogger(__name__)


class PlannerDbStateStore:
    """Loads planner state from backend DB via HTTP API endpoints."""

    async def load_state(self, chat_id: str) -> PlannerState:
        state = PlannerState()

        hypothesis = await BackendClient.get_hypothesis_by_chat(chat_id)
        if hypothesis is None:
            return state

        state.hypothesis = hypothesis

        plan = await BackendClient.get_plan_by_hypothesis(hypothesis.id)
        if plan is not None:
            state.plan = plan
        else:
            # Keep study goal aligned when a hypothesis exists but plan is missing.
            state.plan = StudyPlan(hypothesis_id=hypothesis.id, study_goal=hypothesis.title)

        return state
