import logging
logger = logging.getLogger(__name__)
from fastapi import APIRouter, UploadFile, File, Query, HTTPException
import shutil
import json
import os

# We create a new router specifically for population/cohort tasks
router = APIRouter(prefix="/populations", tags=["populations"])

@router.post("/upload-targets")
async def upload_targets(file: UploadFile = File(...), session_id: str = Query(None)):
    workspace_dir = os.environ.get("WORKSPACE_DIR", "./workspace")
    # If a session_id is provided, scope to that session's folder
    if session_id:
        workspace_dir = os.path.join(workspace_dir, session_id)
    output_dir = os.path.join(workspace_dir, 'populations', 'targets')
    os.makedirs(output_dir, exist_ok=True)

    csv_path = os.path.join(output_dir, "targets.csv")

    logger.info(f"========== DEBUG: SAVING FILE TO ==========")
    logger.info(os.path.abspath(csv_path))

    with open(csv_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {"message": "Targets saved successfully", "path": csv_path}


@router.post("/set-labelling-mode")
async def set_labelling_mode(
    session_id: str = Query(..., description="Active session ID"),
    mode: str = Query(..., description="'manual' or 'automatic'")
):
    """
    Writes the chosen cohort labelling mode into the session's config.json.
    The generate_targets MCP tool reads this at runtime to decide which
    labelling path to execute (LLM adjudicator vs. waiting for a manual upload).
    """
    if mode not in ("manual", "automatic"):
        raise HTTPException(status_code=400, detail="mode must be 'manual' or 'automatic'")

    workspace_dir = os.environ.get("WORKSPACE_DIR", "./workspace")
    session_workspace = os.path.join(workspace_dir, session_id)
    config_path = os.path.join(session_workspace, "config.json")

    # Read existing config (may not exist yet — that's fine)
    config: dict = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            config = {}

    config["labelling_mode"] = mode

    os.makedirs(session_workspace, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

    return {"message": f"Labelling mode set to '{mode}'", "session_id": session_id}
