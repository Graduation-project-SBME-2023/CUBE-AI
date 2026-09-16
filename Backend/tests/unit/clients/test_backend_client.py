import unittest

from clients.backend_client import BackendClient
from unittest import IsolatedAsyncioTestCase


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


if __name__ == "__main__":
    unittest.main()
