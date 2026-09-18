"""Bind one graph node to one `agent_runs` row."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Awaitable, Callable, Sequence

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import BaseAgentEvent, BaseChatMessage
from autogen_core import CancellationToken

from services.run_context import PlannedRun

logger = logging.getLogger(__name__)

StartHook = Callable[[PlannedRun], Awaitable[Any]]
FinishHook = Callable[[PlannedRun], Awaitable[Any]]
FailHook = Callable[[PlannedRun, BaseException], Awaitable[Any]]


class RunBoundAgent(BaseChatAgent):
    """Wraps a graph node so its `agent_runs` row opens and closes with it."""

    def __init__(
        self,
        name: str,
        wrapped_agent: BaseChatAgent,
        run: PlannedRun,
        *,
        on_start: StartHook,
        on_finish: FinishHook,
        on_failure: FailHook,
    ) -> None:
        super().__init__(name=name, description=wrapped_agent.description)
        self._wrapped_agent = wrapped_agent
        self._run = run
        self._on_start = on_start
        self._on_finish = on_finish
        self._on_failure = on_failure

    @property
    def run_record(self) -> PlannedRun:
        return self._run

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return self._wrapped_agent.produced_message_types

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        response: Response | None = None
        async for item in self.on_messages_stream(messages, cancellation_token):
            if isinstance(item, Response):
                response = item
        if response is None:
            raise RuntimeError(f"{self.name} produced no response")
        return response

    async def on_messages_stream(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage | Response, None]:
        # A failure to claim the run is fatal for this step: executing work
        # that no row describes is exactly what current exists to stop.
        await self._on_start(self._run)
        try:
            async for item in self._wrapped_agent.on_messages_stream(
                messages, cancellation_token
            ):
                yield item
        except asyncio.CancelledError as exc:
            # A cancelled step is interrupted, not failed: the run stays
            # `running` so a resume continues the SAME run rather than
            # recording a failure that did not happen.
            logger.info("[%s] step cancelled; leaving the run open", self.name)
            raise
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            await self._on_failure(self._run, exc)
            raise
        else:
            await self._on_finish(self._run)

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        await self._wrapped_agent.on_reset(cancellation_token)
