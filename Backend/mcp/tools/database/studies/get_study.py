import httpx
from ... import mcp


@mcp.tool()
async def get_study(study_uid: int | None = None, skip:int = 0, limit:int=100):
    async with httpx.AsyncClient() as client:
        if study_uid is not None:
            response = await client.get(f"http://localhost:8000/studies/{study_uid}")
        else:
            response = await client.get(
                "http://localhost:8000/studies/",
                params={"skip": skip, "limit": limit}
            )

        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}
    
@mcp.tool()
async def get_study_by_patient(patient_id:int):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://localhost:8000/studies/by_patient/{patient_id}")
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}