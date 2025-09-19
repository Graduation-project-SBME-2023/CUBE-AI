# endpoints/test_dicom.py
from fastapi import APIRouter, Query
from monailabel.interfaces.utils.app import app_instance

router = APIRouter()

@router.get("/test-get-label-dir-dcm")
async def test_get_label_dir_dcm(image_id: str = Query(...)):
    ds = app_instance().datastore()
    if hasattr(ds, "get_label_dir_dcm"):
        return {"result": ds.get_label_dir_dcm(image_id)}
    return {"error": "Method not found"}
