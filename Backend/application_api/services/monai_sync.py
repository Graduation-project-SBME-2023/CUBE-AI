import logging
import os

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from application_api.repositories.inference_models import upsert_model

logger = logging.getLogger(__name__)

MONAI_URL = os.environ.get(
    "INFERENCE_API_URL",
    os.environ.get("MONAI_URL", "http://monailabel:8000"),
).rstrip("/")


async def sync_models_from_monai(db: AsyncSession) -> dict:
    """Sync the model catalog from MONAI Label's /info/ endpoint.

    upsert_model commits per model (it needs its own transaction to run the
    atomic INSERT ... ON CONFLICT). A failure on one model (e.g. a
    malformed config that violates the models.config CHECK constraint) is
    therefore caught and recorded rather than left to abort the whole loop
    silently partway through — every model MONAI reports is always
    attempted, and the caller can see exactly which ones failed.
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{MONAI_URL}/info/", timeout=10.0)
        response.raise_for_status()
        data = response.json()

    results = {"inserted": [], "updated": [], "unchanged": [], "failed": []}

    for model_name, model_info in data.get("models", {}).items():
        model_data = {
            "name":        model_name,
            "type":        model_info.get("type"),
            "labels":      model_info.get("labels"),
            "dimension":   model_info.get("dimension"),
            "description": model_info.get("description"),
            "config":      model_info.get("config"),
        }
        try:
            status = await upsert_model(db, model_data)
        except Exception:
            logger.exception("Failed to upsert model %s during MONAI sync", model_name)
            results["failed"].append(model_name)
            continue
        results[status].append(model_name)

    return results
