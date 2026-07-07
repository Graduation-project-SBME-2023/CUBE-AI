"""
Planner Database State Store

Provides database-backed state persistence for the ClinicalPlanner.
Loads planner state (hypothesis + study plan) from the backend database
via HTTP API endpoints through the BackendClient.

This enables state recovery across server restarts and session resumption.
"""

import logging

from clients.backend_client import BackendClient
from models.plans import PlannerState, StudyPlan

logger = logging.getLogger(__name__)


class PlannerDbStateStore:
    """
    Loads and reconstructs planner state from the backend database.

    This class acts as a data access layer between the ClinicalPlanner
    and the backend HTTP API. It reconstructs a complete PlannerState
    object by fetching the hypothesis and associated study plan for a
    given chat session.

    Usage:
        store = PlannerDbStateStore()
        state = await store.load_state(chat_id)

    The returned PlannerState is guaranteed to have:
        - hypothesis: The persisted ClinicalHypothesis (or empty default)
        - plan: The associated StudyPlan with steps (or empty plan aligned
                to the hypothesis title)

    Attributes:
        None (stateless — all state is fetched from the backend per call).
    """

    async def load_state(self, chat_id: str) -> PlannerState:
        # Start with a clean default state
        state = PlannerState()

        # Fetch hypothesis for this chat session
        hypothesis = await BackendClient.get_hypothesis_by_chat(chat_id)
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
        plan = await BackendClient.get_plan_by_hypothesis(hypothesis.id)
        if plan is not None:
            state.plan = plan
            logger.info(
                "Loaded plan with %d steps for hypothesis_id=%s",
                len(plan.steps),
                hypothesis.id,
            )
        else:
            # No plan yet — create an empty plan aligned to the hypothesis
            # This ensures the planner has a consistent plan object to work with
            state.plan = StudyPlan(
                hypothesis_id=hypothesis.id,
                study_goal=hypothesis.title,
            )
            logger.info(
                "No plan found for hypothesis_id=%s, created empty plan "
                "with study_goal='%s'",
                hypothesis.id,
                hypothesis.title,
            )

        return state