import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import mcp

# Import all your tool modules so their @mcp.tool() decorators get registered
from tools.image_loading_tools import load_image_tool
from tools.get_patients_info import get_all_patients_info
from tools.get_studies_info import get_all_studies_info
from tools.database.models.get_model import get_model
from tools.database.patients.get_patient import get_patient
from tools.database.studies.get_study import get_study, get_study_by_patient
# from tools.database.models.get_models import get_models
# from tools.database.models.create_model import create_model

# For FastMCP, this is the correct way to run with stdio
if __name__ == "__main__":
    mcp.run(transport="stdio")