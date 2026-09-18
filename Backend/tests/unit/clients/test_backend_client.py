import unittest

import pytest

from clients.backend_client import BackendClient
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

# This file had no marker at all, so `-m "unit or contract or pipeline"`
# (the deterministic gate) silently deselected every test in it.
pytestmark = pytest.mark.unit


class BackendClientIntentTests(unittest.TestCase):
    """`_parse_intent` replaced `_parse_hypothesis` in the database-backed flow."""

    SESSION = {
        "session_id": "249b9571-f850-4b54-924f-73a19bbc4060",
        "title": "Draft hypothesis",
        "status": "planning",
        "prompt": "Initial research question",
        "prompt_kind": "hypothesis",
        "intent": {
            "schema_version": 1,
            "title": "Draft hypothesis",
            "population": None,
            "intervention": None,
            "comparison": None,
            "outcome": None,
            "clinical_context": None,
            "relationship_type": None,
            "statement": None,
        },
    }

    def test_parse_intent_accepts_nullable_draft_fields(self):
        """The planner persists a draft before it has filled the PICO fields,
        so nulls are legitimate, not an error."""
        hypothesis = BackendClient._parse_intent(self.SESSION)

        self.assertEqual(hypothesis.population, "")
        self.assertEqual(hypothesis.intervention, "")
        self.assertEqual(hypothesis.comparison, "")
        self.assertEqual(hypothesis.outcome, "")
        self.assertEqual(hypothesis.clinical_context, "")
        self.assertEqual(hypothesis.relationship_type, "")
        self.assertEqual(hypothesis.title, "Draft hypothesis")

    def test_parse_intent_carries_the_users_own_words(self):
        """Defect 1: this field used to be discarded on every parse."""
        hypothesis = BackendClient._parse_intent(self.SESSION)
        self.assertEqual(hypothesis.question, "Initial research question")

    def test_parse_intent_survives_a_session_with_no_intent_yet(self):
        """A `general` prompt has no PICO structure at all, and that is a
        first-class case — not a degraded one."""
        hypothesis = BackendClient._parse_intent(
            {
                "session_id": "249b9571-f850-4b54-924f-73a19bbc4060",
                "title": None,
                "status": "planning",
                "prompt": "which of my CT series have liver masks?",
                "prompt_kind": "general",
                "intent": None,
            }
        )

        self.assertEqual(hypothesis.question, "which of my CT series have liver masks?")
        self.assertEqual(hypothesis.population, "")
        self.assertEqual(hypothesis.title, "")

    def test_intent_payload_never_copies_the_prompt(self):
        """`sessions.intent` is the planner's reading of the prompt. A second
        copy of the prompt text inside it could disagree with the immutable
        original, and nothing would hold the two equal."""
        from models.plans import ClinicalHypothesis

        payload = BackendClient._build_intent_payload(
            ClinicalHypothesis(
                title="T",
                population="P",
                intervention="I",
                comparison="C",
                outcome="O",
                hypothesis="statement text",
                question="the user's own words",
            )
        )

        self.assertNotIn("the user's own words", payload.values())
        self.assertNotIn("question", payload)
        self.assertEqual(payload["statement"], "statement text")
        self.assertEqual(payload["schema_version"], 1)


class BackendClientBoundaryTests(unittest.TestCase):
    def test_non_uuid_session_ids_are_rejected_not_skipped(self):
        """A failed session create must be a visible error, never a local
        phantom run that no row describes."""
        with self.assertRaises(ValueError):
            BackendClient._require_uuid("local-1234", "session_id")
        with self.assertRaises(ValueError):
            BackendClient._require_uuid(None, "session_id")

        self.assertEqual(
            BackendClient._require_uuid(
                "249b9571-f850-4b54-924f-73a19bbc4060", "session_id"
            ),
            "249b9571-f850-4b54-924f-73a19bbc4060",
        )


class BackendClientReportTests(IsolatedAsyncioTestCase):
    """`create_report` is the literature-review report row this session's
    fix wires up (V1-PLAN item 25) — a report keyed by its literature review
    rather than by an agent run."""

    async def test_create_report_rejects_non_uuid_session_id(self):
        """The same fail-loud boundary as every other write: a bad session
        id must never silently become a local phantom write."""
        with self.assertRaises(ValueError):
            await BackendClient.create_report(
                session_id="local-1234",
                source_text="# Literature Review",
                literature_review_id="249b9571-f850-4b54-924f-73a19bbc4060",
                source_format="markdown",
            )


class BackendClientSaveIntentTests(IsolatedAsyncioTestCase):
    """`sessions.title` is only ever set at creation — almost always blank,
    since a hypothesis session is created from the raw prompt, not a title —
    and the dashboard history list reads the lightweight SessionSummary
    schema, which has no `intent` field to fall back to. Without mirroring,
    every history card showed the raw, untruncated prompt instead of the
    planner's title."""

    SESSION_ID = "249b9571-f850-4b54-924f-73a19bbc4060"

    async def test_save_intent_mirrors_the_title_onto_the_plain_column(self):
        from models.plans import ClinicalHypothesis

        with patch.object(
            BackendClient, "update_session", new=AsyncMock(return_value={"session_id": self.SESSION_ID})
        ) as mock_update:
            await BackendClient.save_intent(
                self.SESSION_ID,
                ClinicalHypothesis(
                    title="Whole-Spleen Radiomics Classification",
                    population="P",
                    intervention="I",
                    comparison="C",
                    outcome="O",
                    hypothesis="statement text",
                    question="the user's own words",
                ),
            )

        mock_update.assert_awaited_once()
        _, payload = mock_update.await_args.args
        assert payload["title"] == "Whole-Spleen Radiomics Classification"

    async def test_save_intent_falls_back_to_a_placeholder_title_for_a_blank_draft(self):
        """A draft intent (planning still in progress, title not yet known)
        must still mirror *something* other than the raw prompt — matching
        `_build_intent_payload`'s own "Untitled Hypothesis" default, never a
        blank column that would make the frontend fall back to the prompt."""
        from models.plans import ClinicalHypothesis

        with patch.object(
            BackendClient, "update_session", new=AsyncMock(return_value={"session_id": self.SESSION_ID})
        ) as mock_update:
            await BackendClient.save_intent(
                self.SESSION_ID,
                ClinicalHypothesis(
                    title="",
                    population="",
                    intervention="",
                    comparison="",
                    outcome="",
                    hypothesis="",
                    question="the user's own words",
                ),
            )

        mock_update.assert_awaited_once()
        _, payload = mock_update.await_args.args
        assert payload["title"] == "Untitled Hypothesis"


if __name__ == "__main__":
    unittest.main()
