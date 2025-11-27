from tools import mcp
from fastapi import FastAPI
import uvicorn
from tools.characterization_tools import characterize_tumor
from tools.image_loading_tools import load_image_tool
from tools.radiomics_analysis_tool import radiomics_analysis_tool
from tools.segmentation_tools import segment_volume_tool
from tools.get_patients_info import get_all_patients_info
from tools.get_studies_info import get_all_studies_info
from tools.database.models.get_model import get_model
from tools.database.patients.get_patient import get_patient
from tools.database.studies.get_study import get_study, get_study_by_patient
# from tools.database.models.get_models import get_models
# from tools.database.models.create_model import create_model



if __name__ == "__main__":
    mcp.run(transport="stdio")
