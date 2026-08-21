from collections.abc import Mapping
from typing import Any

from autogen_core import CancellationToken
from autogen_core.tools import BaseTool

from .resource_scheduler import ResourceScheduler, resource_scheduler


class ResourceGuardedTool(BaseTool):
    """Transparent AutoGen tool wrapper that holds a resource lease while running."""

    def __init__(
        self,
        delegate: BaseTool,
        *,
        resources: tuple[str, ...],
        session_id: str,
        agent_id: str,
        scheduler: ResourceScheduler = resource_scheduler,
    ) -> None:
        super().__init__(
            args_type=delegate.args_type(),
            return_type=delegate.return_type(),
            name=delegate.name,
            description=delegate.description,
            strict=bool(delegate.schema.get("strict", False)),
        )
        self._delegate = delegate
        self._resources = resources
        self._session_id = session_id
        self._agent_id = agent_id
        self._scheduler = scheduler

    async def run(self, args: Any, cancellation_token: CancellationToken) -> Any:
        async with self._scheduler.lease(
            self._resources,
            session_id=self._session_id,
            operation=f"{self._agent_id}.{self.name}",
            agent_id=self._agent_id,
        ):
            return await self._delegate.run(args, cancellation_token)

    def return_value_as_string(self, value: Any) -> str:
        return self._delegate.return_value_as_string(value)

    async def save_state_json(self) -> Mapping[str, Any]:
        return await self._delegate.save_state_json()

    async def load_state_json(self, state: Mapping[str, Any]) -> None:
        await self._delegate.load_state_json(state)


def guard_named_tools(
    tools: list[BaseTool],
    resource_by_name: dict[str, tuple[str, ...]],
    *,
    session_id: str,
    agent_id: str,
) -> list[BaseTool]:
    return [
        ResourceGuardedTool(
            tool,
            resources=resource_by_name[tool.name],
            session_id=session_id,
            agent_id=agent_id,
        )
        if tool.name in resource_by_name
        else tool
        for tool in tools
    ]
