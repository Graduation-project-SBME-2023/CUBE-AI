from tools import mcp
from fastapi import FastAPI
import uvicorn
# from tools.characterization_tools import characterize_tumor
# from tools.image_loading_tools import load_image_tool
# from tools.radiomics_analysis_tool import radiomics_analysis_tool
# from tools.segmentation_tools import segment_volume_tool
from tools.get_patients_info import get_all_patients_info
from tools.get_studies_info import get_all_studies_info
from tools.database.models.get_model import get_model
from tools.database.patients.get_patient import get_patient
from tools.database.studies.get_study import get_study, get_study_by_patient
from tools.database.volumes.get_volume import get_volume, get_volumes_by_study
from tools.database.volumes.count_volumes import count_volumes_in_study
from tools.database.patients.search_patient import search_patient
from tools.database.studies.search_studies import search_studies
from tools.database.volumes.search_volumes import search_volumes
from tools.database.segmentations.get_segmentation import get_segmentation, get_invalidate_segmentations, get_segmentation_by_model, get_segmentation_by_volume
from tools.database.segmentations.search_segmentation import search_segmentation
from tools.database.segmentations.segmentations_status import get_segmentation_stats
from tools.save_population import save_final_output
from tools.latex.get_final_report import get_final_report
from tools.latex.latex_to_pdf import latex_to_pdf
from tools.latex.template_tools import get_latex_template, list_latex_templates
from tools.get_modeling_results import get_modeling_results
from tools.targets_matcher import generate_targets


from tools.run_segmentation import run_segmentation
from tools.check_having_mask import check_having_mask
from tools.list_models import list_models
# from tools.database.models.get_models import get_models
# from tools.database.models.create_model import create_model
import tools.population.inclusion_criteria
import tools.population.unique_descriptions

import tools.scratchpad
import tools.agent_output
import tools.literature_context
import tools.task_context
from tools.modeling.classification import pycaret_train_classifier, pycaret_run_inference
from tools.modeling.regression import pycaret_train_regressor, pycaret_run_regression_inference

from tools.feature_extraction.features import radiomics_extract_features, radiomics_analyze_importance


if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    except Exception as e:
        import traceback
        traceback.print_exc()
