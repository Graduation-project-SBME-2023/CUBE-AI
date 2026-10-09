"""The LLM a tool calls by itself (merged plan, Phase 2.3).

``extract_cohort_labels`` reads clinical reports with an LLM. It used to get
the endpoint from ``MODEL_BASE_URL`` / ``MODEL_NAME`` / ``MODEL_API_KEY``, which
the AutoGen population agent copied from ``client_config`` into the MCP
subprocess. The tool now reads ``client_config`` itself, the same way (the
active provider's base URL, model and key; ``OLLAMA_BASE_URL`` when the
configuration names no base URL). Plan 9 replaces this module with the Model
Gateway's ``extraction`` profile.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

# The same module object the app's /client-settings route updates: under the
# root the packs were imported with (see toolpacks/_toolkit.py).
if __name__.startswith("agent_system."):
    from agent_system import client_config
else:
    import client_config

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_API_KEY = "ollama"  # an OpenAI-compatible server needs some key


@dataclass(frozen=True)
class LlmEndpoint:
    base_url: str
    model: str
    api_key: str


def extraction_endpoint() -> LlmEndpoint:
    """The OpenAI-compatible endpoint for the current client configuration."""
    config = client_config.get_client_config()
    return LlmEndpoint(
        base_url=config.base_url or os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
        model=config.model,
        api_key=config.api_key or DEFAULT_API_KEY,
    )


@asynccontextmanager
async def openai_client(endpoint: LlmEndpoint) -> AsyncIterator[Any]:
    """An ``AsyncOpenAI`` client for ``endpoint``, closed when the block ends."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=endpoint.base_url, api_key=endpoint.api_key)
    try:
        yield client
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            await close()
