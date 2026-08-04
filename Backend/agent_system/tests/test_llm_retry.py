import unittest
from unittest.mock import patch

from client_config import add_model_client_retries


class _ServerError(Exception):
    status_code = 500


class _CreateClient:
    def __init__(self, failures: int, error_type=_ServerError):
        self.calls = 0
        self.failures = failures
        self.error_type = error_type

    async def create(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error_type("model call failed")
        return "ok"

    async def create_stream(self, *args, **kwargs):
        yield "ok"


class _StreamClient:
    def __init__(self, failures: int, *, emit_before_failure: bool = False):
        self.calls = 0
        self.failures = failures
        self.emit_before_failure = emit_before_failure

    async def create(self, *args, **kwargs):
        return "ok"

    async def create_stream(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            if self.emit_before_failure:
                yield "partial"
            raise _ServerError("stream failed")
        yield "complete"


class ModelClientRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_500_retries_three_times_then_succeeds(self):
        client = _CreateClient(failures=3)
        with patch("client_config.LLM_RETRY_DELAYS_SECONDS", (0.0, 0.0, 0.0)):
            add_model_client_retries(client)
            result = await client.create()

        self.assertEqual(result, "ok")
        self.assertEqual(client.calls, 4)

    async def test_non_retryable_error_fails_immediately(self):
        class RequestError(Exception):
            status_code = 400

        client = _CreateClient(failures=1, error_type=RequestError)
        with patch("client_config.LLM_RETRY_DELAYS_SECONDS", (0.0, 0.0, 0.0)):
            add_model_client_retries(client)
            with self.assertRaises(RequestError):
                await client.create()

        self.assertEqual(client.calls, 1)

    async def test_stream_retries_only_before_first_output(self):
        client = _StreamClient(failures=2)
        with patch("client_config.LLM_RETRY_DELAYS_SECONDS", (0.0, 0.0, 0.0)):
            add_model_client_retries(client)
            chunks = [chunk async for chunk in client.create_stream()]

        self.assertEqual(chunks, ["complete"])
        self.assertEqual(client.calls, 3)

    async def test_partial_stream_is_not_replayed(self):
        client = _StreamClient(failures=1, emit_before_failure=True)
        with patch("client_config.LLM_RETRY_DELAYS_SECONDS", (0.0, 0.0, 0.0)):
            add_model_client_retries(client)
            with self.assertRaises(_ServerError):
                _ = [chunk async for chunk in client.create_stream()]

        self.assertEqual(client.calls, 1)


if __name__ == "__main__":
    unittest.main()
