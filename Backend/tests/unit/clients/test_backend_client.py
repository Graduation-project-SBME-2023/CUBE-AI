import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from clients.backend_client import BASE_URL, WS_LOG_PAGE_SIZE, BackendClient


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


class BackendClientLogTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _log(index: int) -> dict:
        return {
            "uuid": f"event-{index}",
            "session_id": "session-a",
            "msg_type": "tool_output",
            "payload": {"index": index},
        }

    @staticmethod
    def _response(data: list[dict]) -> MagicMock:
        response = MagicMock()
        response.json.return_value = data
        return response

    async def test_get_logs_fetches_every_page_in_order(self):
        first_page = [self._log(index) for index in range(100)]
        second_page = [self._log(index) for index in range(100, 102)]
        client = AsyncMock()
        client.get.side_effect = [
            self._response(first_page),
            self._response(second_page),
        ]
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "clients.backend_client.httpx.AsyncClient",
            return_value=context,
        ):
            logs = await BackendClient.get_logs("session-a")

        self.assertEqual([log.payload["index"] for log in logs], list(range(102)))
        self.assertEqual(
            client.get.await_args_list,
            [
                call(
                    f"{BASE_URL}/ws_logs/session/session-a",
                    params={"skip": 0, "limit": WS_LOG_PAGE_SIZE},
                ),
                call(
                    f"{BASE_URL}/ws_logs/session/session-a",
                    params={"skip": 100, "limit": WS_LOG_PAGE_SIZE},
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
