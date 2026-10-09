"""An expected failure, raised from deep inside a tool's steps.

A multi-step tool (extraction, training) raises ``fail(code, message, ...)``
where a step finds a problem, and its entry point returns ``failure.result``:
the ``error_result`` envelope, with no traceback and no ``(ok, error)`` tuples
threaded through every step.
"""

from __future__ import annotations

from typing import Any

from .. import _toolkit


class ToolFailure(Exception):
    """Carries the ``error_result`` the tool returns."""

    def __init__(self, result: dict[str, Any]):
        super().__init__(result["error_message"])
        self.result = result


def fail(code: str, message: str, **extra: Any) -> ToolFailure:
    """``raise fail(...)``: the tool answers ``error_result(code, message, **extra)``."""
    return ToolFailure(_toolkit.error_result(code, message, **extra))


__all__ = ["ToolFailure", "fail"]
