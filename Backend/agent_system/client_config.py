"""
Centralized model-client configuration.

Stores the active LLM provider/model settings and exposes a factory
function that creates the appropriate AutoGen ChatCompletionClient.

Supported providers
───────────────────
  • openai        → OpenAIChatCompletionClient
  • azure_openai  → AzureOpenAIChatCompletionClient
  • ollama        → OpenAIChatCompletionClient (OpenAI-compatible mode)
"""

from __future__ import annotations

import asyncio
import logging
import os
import json
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _retry_delays() -> tuple[float, ...]:
    raw = os.environ.get("LLM_RETRY_DELAYS_SECONDS", "1,4,16")
    try:
        delays = tuple(float(value.strip()) for value in raw.split(","))
    except (TypeError, ValueError):
        return (1.0, 4.0, 16.0)
    if not delays or any(value < 0 for value in delays):
        return (1.0, 4.0, 16.0)
    return delays


LLM_RETRY_DELAYS_SECONDS = _retry_delays()


def _is_retryable_llm_error(exc: Exception) -> bool:
    current: BaseException | None = exc
    for _ in range(4):
        if current is None:
            break
        status_code = getattr(current, "status_code", None)
        response = getattr(current, "response", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)
        if status_code in {408, 409, 429} or (
            isinstance(status_code, int) and status_code >= 500
        ):
            return True
        if isinstance(current, (TimeoutError, ConnectionError)):
            return True
        if type(current).__name__ in {
            "APIConnectionError",
            "APITimeoutError",
            "ConnectError",
            "ConnectTimeout",
            "InternalServerError",
            "NetworkError",
            "RateLimitError",
            "ReadError",
            "ReadTimeout",
            "RemoteProtocolError",
        }:
            return True
        current = current.__cause__ or current.__context__
    return False


def _token_is_cancelled(token: Any) -> bool:
    if token is None:
        return False
    value = getattr(token, "is_cancelled", False)
    try:
        return bool(value() if callable(value) else value)
    except Exception:
        return False


async def _wait_before_llm_retry(delay: float, cancellation_token: Any) -> None:
    deadline = asyncio.get_running_loop().time() + delay
    while True:
        if _token_is_cancelled(cancellation_token):
            raise asyncio.CancelledError()
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(0.25, remaining))


def add_model_client_retries(client):
    """Retry transient model failures before AutoGen fails the whole run."""
    if getattr(client, "_cube_retry_wrapped", False):
        return client

    original_create = client.create
    original_create_stream = getattr(client, "create_stream", None)

    async def create_with_retry(*args, **kwargs):
        cancellation_token = kwargs.get("cancellation_token")
        for attempt in range(len(LLM_RETRY_DELAYS_SECONDS) + 1):
            try:
                return await original_create(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if (
                    attempt >= len(LLM_RETRY_DELAYS_SECONDS)
                    or not _is_retryable_llm_error(exc)
                ):
                    raise
                delay = LLM_RETRY_DELAYS_SECONDS[attempt]
                logger.warning(
                    "Transient LLM %s on attempt %s/%s; retrying in %.1fs",
                    type(exc).__name__,
                    attempt + 1,
                    len(LLM_RETRY_DELAYS_SECONDS) + 1,
                    delay,
                )
                await _wait_before_llm_retry(delay, cancellation_token)

    async def create_stream_with_retry(*args, **kwargs):
        cancellation_token = kwargs.get("cancellation_token")
        for attempt in range(len(LLM_RETRY_DELAYS_SECONDS) + 1):
            emitted = False
            try:
                async for chunk in original_create_stream(*args, **kwargs):
                    emitted = True
                    yield chunk
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if (
                    emitted
                    or attempt >= len(LLM_RETRY_DELAYS_SECONDS)
                    or not _is_retryable_llm_error(exc)
                ):
                    raise
                delay = LLM_RETRY_DELAYS_SECONDS[attempt]
                logger.warning(
                    "Transient streaming LLM %s on attempt %s/%s; retrying in %.1fs",
                    type(exc).__name__,
                    attempt + 1,
                    len(LLM_RETRY_DELAYS_SECONDS) + 1,
                    delay,
                )
                await _wait_before_llm_retry(delay, cancellation_token)

    client.create = create_with_retry
    if callable(original_create_stream):
        client.create_stream = create_stream_with_retry
    client._cube_retry_wrapped = True
    return client


# ── Configuration Model ─────────────────────────────────────────────────────

class ClientConfig(BaseModel):
    """User-configurable LLM client settings."""

    provider: Literal["openai", "azure_openai", "ollama"] = "ollama"
    model: str = "gpt-oss:120b-cloud"
    api_key: Optional[str] = None
    base_url: Optional[str] = None

    # Azure-specific
    azure_endpoint: Optional[str] = None
    azure_deployment: Optional[str] = None
    api_version: Optional[str] = None

    # Model capabilities
    vision: bool = False
    function_calling: bool = True
    json_output: bool = True
    structured_output: bool = True

    # Generation parameters
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None


# ── Global Store ─────────────────────────────────────────────────────────────

CONFIG_FILE = Path.home() / ".cube" / "client_config.json"

def load_config_from_disk() -> ClientConfig | None:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ClientConfig(**data)
        except Exception as e:
            logger.error("Failed to load client config from disk: %s", e)
    return None

def save_config_to_disk(config: ClientConfig) -> None:
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, indent=2)
    except Exception as e:
        logger.error("Failed to save client config to disk: %s", e)

_current_config = load_config_from_disk() or ClientConfig(
    api_key=os.getenv("OLLAMA_API_KEY"),
)

def get_client_config() -> ClientConfig:
    """Return the current global client configuration."""
    return _current_config


def set_client_config(config: ClientConfig) -> None:
    """Replace the global client configuration."""
    global _current_config
    _current_config = config
    logger.info(
        "Client config updated: provider=%s  model=%s",
        config.provider,
        config.model,
    )


# ── Factory ──────────────────────────────────────────────────────────────────

def create_model_client_from_config(config: ClientConfig | None = None):
    """
    Build and return the correct AutoGen ChatCompletionClient
    based on the given (or current global) configuration.
    """
    if config is None:
        config = _current_config

    model_info: Dict[str, Any] = {
        "vision": config.vision,
        "function_calling": config.function_calling,
        "json_output": config.json_output,
        "structured_output": config.structured_output,
        "family": "unknown",
    }

    if config.provider == "openai":
        from autogen_ext.models.openai import OpenAIChatCompletionClient

        kwargs: Dict[str, Any] = {
            "model": config.model,
            "model_info": model_info,
        }
        if config.api_key:
            kwargs["api_key"] = config.api_key
        if config.base_url:
            kwargs["base_url"] = config.base_url
        if config.temperature is not None:
            kwargs["temperature"] = config.temperature
        if config.max_tokens is not None:
            kwargs["max_tokens"] = config.max_tokens
        if config.top_p is not None:
            kwargs["top_p"] = config.top_p
        return add_model_client_retries(OpenAIChatCompletionClient(**kwargs))

    elif config.provider == "azure_openai":
        from autogen_ext.models.openai import AzureOpenAIChatCompletionClient

        kwargs = {
            "model": config.model,
            "model_info": model_info,
            "azure_endpoint": config.azure_endpoint or "",
            "api_version": config.api_version or "2024-06-01",
        }
        if config.azure_deployment:
            kwargs["azure_deployment"] = config.azure_deployment
        if config.api_key:
            kwargs["api_key"] = config.api_key
        if config.temperature is not None:
            kwargs["temperature"] = config.temperature
        if config.max_tokens is not None:
            kwargs["max_tokens"] = config.max_tokens
        if config.top_p is not None:
            kwargs["top_p"] = config.top_p
        return add_model_client_retries(AzureOpenAIChatCompletionClient(**kwargs))

    else:  # ollama — use OpenAI-compatible endpoint
        from autogen_ext.models.openai import OpenAIChatCompletionClient

        kwargs = {
            "model": config.model,
            "model_info": model_info,
            "base_url": config.base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            "api_key": config.api_key or "ollama",
        }
        if config.temperature is not None:
            kwargs["temperature"] = config.temperature
        if config.max_tokens is not None:
            kwargs["max_tokens"] = config.max_tokens
        if config.top_p is not None:
            kwargs["top_p"] = config.top_p
        return add_model_client_retries(OpenAIChatCompletionClient(**kwargs))
