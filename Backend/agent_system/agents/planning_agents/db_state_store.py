"""Planner Database State Store"""

import logging

from clients.backend_client import BackendClient
from models.plans import PlannerState, StudyPlan

logger = logging.getLogger(__name__)


class PlannerDbStateStore:
    """Loads and reconstructs planner state from the backend database."""

    async def load_state(self, chat_id: str) -> PlannerState:
        # Start with a clean default state
        state = PlannerState()

        # Fetch hypothesis for this chat session
        hypothesis = await BackendClient.get_intent(chat_id)
        if hypothesis is None:
            logger.info(
                "No hypothesis found for chat_id=%s, returning empty state",
                chat_id,
            )
            return state

        # Hypothesis found — populate state
        state.hypothesis = hypothesis
        logger.info(
            "Loaded hypothesis '%s' (id=%s) for chat_id=%s",
            hypothesis.title,
            hypothesis.id,
            chat_id,
        )

        # Fetch associated study plan
        plan = await BackendClient.get_plan_by_session(chat_id)
        if plan is not None:
            state.plan = plan
            logger.info(
                "Loaded plan with %d steps for session_id=%s",
                len(plan.steps),
                chat_id,
            )
        else:
            # The session's plan row always exists (it is created in the same
            # transaction as the session), so this is only the in-memory
            # placeholder for a draft that has no steps yet.
            state.plan = StudyPlan(id=str(chat_id), session_id=str(chat_id))
            logger.info(
                "No plan steps yet for session_id=%s; starting from an empty draft",
                chat_id,
            )

        return state