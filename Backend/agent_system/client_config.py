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

import logging
import os
import json
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


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
        return OpenAIChatCompletionClient(**kwargs)

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
        return AzureOpenAIChatCompletionClient(**kwargs)

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
        return OpenAIChatCompletionClient(**kwargs)
