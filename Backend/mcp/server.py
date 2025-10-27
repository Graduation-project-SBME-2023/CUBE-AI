from tools import mcp
from fastapi import FastAPI
import uvicorn
from tools.characterization_tools import characterize_tumor
from tools.image_loading_tools import load_image_tool
from tools.radiomics_analysis_tool import radiomics_analysis_tool
from tools.segmentation_tools import segment_volume_tool



if __name__ == "__main__":
    mcp.run(transport="stdio")
