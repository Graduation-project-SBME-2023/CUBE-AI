import unittest

from models.plans import ClinicalHypothesis
from models.session import Session
from services.session_service import (
    SessionPlanningConflict,
    SessionService,
)


class _NoopWebSocketManager:
    async def send_ws_message(self, *_args, **_kwargs):
        return None


class PlanningDraftGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_allows_planning_from_persisted_free_text_draft(self):
        service = SessionService(_NoopWebSocketManager())
        session = Session(session_id="draft-session")
        session.hypothesis = ClinicalHypothesis(
            title="Draft",
            hypothesis="Initial research question",
            state="not_confirmed",
        )
        service.store.put(session)

        planner_called = False

        async def planner(_session, _message):
            nonlocal planner_called
            planner_called = True

        await service.start_planning(session.session_id, "question", planner)
        await session.planning_task

        self.assertTrue(planner_called)

    async def test_rejects_overwrite_of_structured_hypothesis(self):
        service = SessionService(_NoopWebSocketManager())
        session = Session(session_id="structured-session")
        session.hypothesis = ClinicalHypothesis(
            title="Structured",
            population="Adults",
            intervention="CT radiomics",
            state="not_confirmed",
        )
        service.store.put(session)

        async def planner(_session, _message):
            return None

        with self.assertRaises(SessionPlanningConflict):
            await service.start_planning(session.session_id, "question", planner)


if __name__ == "__main__":
    unittest.main()
