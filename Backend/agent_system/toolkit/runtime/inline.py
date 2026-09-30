"""The inline runner: awaits the handler on the agent-system event loop."""

from __future__ import annotations

from typing import Any

from ..context import ToolContext
from ..tools.spec import ToolSpec
from .cancel import CancelToken


async def run_inline(
    spec: ToolSpec, kwargs: dict[str, Any], ctx: ToolContext, cancel: CancelToken | None
) -> Any:
    """Call the handler, giving it the context if it asked for one. Cancelling
    the surrounding task is enough to stop it, so ``cancel`` is not watched here."""
    if spec.context_param is not None:
        kwargs = {**kwargs, spec.context_param: ctx}
    return await spec.handler(**kwargs)
