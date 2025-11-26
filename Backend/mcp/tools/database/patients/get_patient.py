import httpx
from ... import mcp


@mcp.tool()
async def get_patient(patient_id: int | None = None, skip:int = 0, limit:int=100):
    async with httpx.AsyncClient() as client:
        if patient_id is not None:
            response = await client.get(f"http://localhost:8000/patients/{patient_id}")
        else:
            response = await client.get(
                "http://localhost:8000/patients/",
                params={"skip": skip, "limit": limit}
            )

        response.raise_for_status()

        # Wrap response in a dict to satisfy MCP expectations
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}