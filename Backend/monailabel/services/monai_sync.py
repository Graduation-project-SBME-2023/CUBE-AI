import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from crud.model import upsert_model

MONAI_URL = "http://localhost:8000/info/"

# async def sync_models_from_monai(db: AsyncSession) -> dict:
#     async with httpx.AsyncClient() as client:
#         response = await client.get(MONAI_URL, timeout=10.0)
#         response.raise_for_status()
#         data = response.json()

#     models = data.get("models", {})
#     synced = []

#     for model_name, model_info in models.items():
#         model_data = {
#             "name": model_name,
#             "type": model_info.get("type"),
#             "labels": model_info.get("labels"),
#             "dimension": model_info.get("dimension"),
#             "description": model_info.get("description"),
#             "config": model_info.get("config"),
#         }
#         await upsert_model(db, model_data)
#         synced.append(model_name)

#     return {"synced": synced, "count": len(synced)}

async def sync_models_from_monai(db: AsyncSession) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(MONAI_URL, timeout=10.0)
        response.raise_for_status()
        data = response.json()

    results = {"inserted": [], "updated": [], "unchanged": []}

    for model_name, model_info in data.get("models", {}).items():
        model_data = {
            "name":        model_name,
            "type":        model_info.get("type"),
            "labels":      model_info.get("labels"),
            "dimension":   model_info.get("dimension"),
            "description": model_info.get("description"),
            "config":      model_info.get("config"),
        }
        status = await upsert_model(db, model_data)
        results[status].append(model_name)

    return results