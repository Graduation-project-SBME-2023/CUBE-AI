import unittest

from clients.backend_client import BackendClient


class BackendClientHypothesisTests(unittest.TestCase):
    def test_parse_hypothesis_accepts_nullable_draft_fields(self):
        hypothesis = BackendClient._parse_hypothesis(
            {
                "id": "249b9571-f850-4b54-924f-73a19bbc4060",
                "title": "Draft hypothesis",
                "state": "not_confirmed",
                "population": None,
                "intervention": None,
                "comparison": None,
                "outcome": None,
                "clinical_context": None,
                "relationship_type": None,
                "hypothesis": "Initial research question",
            }
        )

        self.assertEqual(hypothesis.population, "")
        self.assertEqual(hypothesis.intervention, "")
        self.assertEqual(hypothesis.comparison, "")
        self.assertEqual(hypothesis.outcome, "")
        self.assertEqual(hypothesis.clinical_context, "")
        self.assertEqual(hypothesis.relationship_type, "")
        self.assertEqual(hypothesis.hypothesis, "Initial research question")


if __name__ == "__main__":
    unittest.main()
