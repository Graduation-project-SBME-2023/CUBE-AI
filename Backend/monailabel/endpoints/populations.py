from fastapi import APIRouter, UploadFile, File
import shutil
import os

# We create a new router specifically for population/cohort tasks
router = APIRouter(prefix="/populations", tags=["populations"])

@router.post("/upload-targets")
async def upload_targets(file: UploadFile = File(...)):
    workspace_dir = os.environ.get("WORKSPACE_DIR", "./workspace")
    output_dir = os.path.join(workspace_dir, 'populations', 'targets')
    os.makedirs(output_dir, exist_ok=True)
    
    csv_path = os.path.join(output_dir, "targets.csv")

    # ADD THESE TWO LINES:
    print(f"========== DEBUG: SAVING FILE TO ==========")
    print(os.path.abspath(csv_path))

    with open(csv_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {"message": "Targets saved successfully", "path": csv_path}