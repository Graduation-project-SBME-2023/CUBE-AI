"""Global LLM client configuration endpoints."""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from client_config import (
    ClientConfig,
    get_client_config,
    set_client_config,
    create_model_client_from_config,
    save_config_to_disk,
)
from agent_system.harness.model_gateway import (
    ModelConfigError,
    alias_choices,
    check_user_aliases,
    load_model_config,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ----------------------------------------------------------------------
# Client configuration endpoints
# ----------------------------------------------------------------------


@router.get("/client-config")
async def get_config():
    """Return the current global LLM client configuration."""
    cfg = get_client_config()
    data = cfg.model_dump()
    # Mask the API key for security
    if data.get("api_key"):
        data["api_key"] = "***"
        data["_masked"] = True
    else:
        data["_masked"] = False
    return {"status": "ok", "config": data}


@router.post("/client-config")
async def update_config(payload: dict):
    """Update the global LLM client configuration with partial merge."""
    try:
        current_dict = get_client_config().model_dump()
        
        for key, value in payload.items():
            if key == "api_key" and value is None:
                current_dict["api_key"] = None
            elif key == "api_key" and (value == "" or value == "***"):
                continue
            elif key == "model_aliases":
                # merged per profile; an empty alias returns the profile to its default
                merged = {**current_dict.get("model_aliases", {}), **(value or {})}
                current_dict[key] = check_user_aliases(load_model_config(), merged)
            else:
                current_dict[key] = value
                
        validated = ClientConfig(**current_dict)
        set_client_config(validated)
        save_config_to_disk(validated)
        
        masked = validated.model_dump()
        if masked.get("api_key"):
            masked["api_key"] = "***"
            masked["_masked"] = True
        else:
            masked["_masked"] = False
        return {"status": "ok", "config": masked}
    except Exception as e:
        logger.error(f"Error updating config: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/api/test-client-config")
async def test_client_config(payload: dict = None):
    try:
        if payload:
            current = get_client_config().model_dump()
            for key, value in payload.items():
                if key == "api_key" and (value == "" or value == "***"):
                    continue
                if value is not None:
                    current[key] = value
            config = ClientConfig(**current)
        else:
            config = get_client_config()
            
        if not config.provider or not config.model:
            raise ValueError("Model configuration incomplete. Please set provider and model.")
            
        client = create_model_client_from_config(config)
        # We need an async block to test connection.
        from autogen_core.models import UserMessage
        # Send a simple test message
        response = await client.create(messages=[UserMessage(content="Respond with exactly the word OK.", source="user")])
        
        return {"status": "ok", "message": "✅ Connection successful! Model is ready."}
    except Exception as e:
        logger.error(f"Error testing client config: {e}")
        return JSONResponse(status_code=400, content={"status": "error", "message": f"❌ Connection failed: {str(e)}"})


@router.get("/client-config/model-profiles")
async def list_model_profiles():
    """The agent engine's model profiles: per profile the alias in use, its
    default and the LiteLLM aliases it may use (config/models.yaml)."""
    try:
        config = load_model_config()
    except ModelConfigError as e:
        logger.error("Cannot read the model profiles: %s", e)
        raise HTTPException(status_code=500, detail="the model profiles cannot be read")
    return {"profiles": alias_choices(config, get_client_config().model_aliases)}


@router.get("/client-config/providers")
async def list_providers():
    """Return available provider options and their required/optional fields."""
    return {
        "providers": [
            {
                "id": "openai",
                "label": "OpenAI",
                "fields": ["model", "api_key"],
                "optional_fields": ["base_url", "temperature", "max_tokens", "top_p"],
                "description": "OpenAI GPT models or any OpenAI-compatible endpoint",
            },
            {
                "id": "azure_openai",
                "label": "Azure OpenAI",
                "fields": ["model", "api_key", "azure_endpoint", "api_version"],
                "optional_fields": [
                    "azure_deployment",
                    "temperature",
                    "max_tokens",
                    "top_p",
                ],
                "description": "Azure-hosted OpenAI models",
            },
            {
                "id": "ollama",
                "label": "Ollama",
                "fields": ["model"],
                "optional_fields": [
                    "base_url",
                    "api_key",
                    "temperature",
                    "max_tokens",
                    "top_p",
                ],
                "description": "Local or remote Ollama instance (OpenAI-compatible)",
            },
        ]
    }
