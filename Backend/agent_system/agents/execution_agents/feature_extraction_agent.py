import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.append(root_dir)

from autogen_agentchat.agents import AssistantAgent
from clients.ollamaClient import OllamaClient
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from descriptions.models_descriptions import FEATURE_EXTRACTION_AGENT_DESCRIPTION
from system_prompts.system_messages import FEATURE_EXTRACTION_AGENT_SYSTEM_PROMPT

from tools.feature_extraction_tools import extract_radiomic_features, analyze_feature_importance
from autogen_core.tools import FunctionTool

class FeatureExtractionAgent(AssistantAgent):
    def __init__(self, name, model_client, tools, description, system_message, max_tool_iterations):
        super().__init__(name=name,
                        model_client=model_client, 
                        tools=tools, 
                        description=description, 
                        system_message=system_message,
                        max_tool_iterations=max_tool_iterations)
    
    @classmethod
    async def create(cls, model_client = None, workspace_folder = None, mcp_server_path='Backend/mcp/server.py'):
        model_client = OllamaClient() if not model_client else model_client

        mcp_server = StdioServerParams(
            command="python", args=[os.path.normpath(mcp_server_path)], env={"WORKSPACE_DIR": workspace_folder}
        )
        mcp_tools = await mcp_server_tools(mcp_server)
        desired_mcp_tools = ['radiomics_extract_features', 'radiomics_analyze_importance', 'scratchpad_register_agent',
                             'scratchpad_write_note', 'scratchpad_raise_flag', 'scratchpad_write_summary',
                             'scratchpad_set_status', 'scratchpad_read_agent', ]
        final_mcp_tools = cls.get_desired_tools(mcp_tools, desired_mcp_tools)

        # feature_extraction_tool_wrapper = FunctionTool(
        #     func=extract_radiomic_features,
        #     description="""
        #     This tool extracts radiomic features from medical images (CT, MRI, etc.) using PyRadiomics.
        #     It processes images and their corresponding segmentation masks, and can handle multiple labels within masks.
        #     Features are extracted for each label and saved separately to CSV files.
        #
        #     Args:
        #         image_types: Image preprocessing types (e.g., ["Original", "Wavelet", "LoG"]) | Optional
        #         specific_features: Specific features to extract (e.g., ["original_firstorder_Mean"]) | Optional
        #         normalize: Whether to normalize image intensities | Optional
        #         bin_width: Bin width for discretizing image intensities | Optional
        #         resample: Whether to resample the image | Optional
        #         pixel_spacing: Target pixel spacing for resampling [x, y, z] | Optional
        #         force_2d: Whether to extract features in 2D mode | Optional
        #         n_workers: Number of parallel workers | Optional
        #         workspace_folder: The workspace folder to output files to | Optional
        #
        #     Returns:
        #         Dictionary with feature extraction results and file paths
        #     """,
        #     name="PyRadiomicsFeatureExtractionTool"
        # )
        #
        # feature_importance_tool_wrapper = FunctionTool(
        #     func=analyze_feature_importance,
        #     description="""
        #     Performs feature importance analysis on tabular data for classification or regression tasks to\
        #     identify the most relevant features using various methods, and outputs CSV files with the selected features.
        #
        #     Analyze feature importance for tabular data and output results.
        #
        #     Args:
        #         input_path: Path to input CSV file
        #         target_column: Target column name for prediction
        #         task_type: 'classification' or 'regression' (auto-detected if None)
        #         method: Feature selection method ('rf', 'f_test', 'mutual_info', 'rfe')
        #         top_features: Comma-separated list of top features to select
        #         encode_categorical: Method for encoding categorical features
        #         max_onehot_cardinality: Max unique values for one-hot encoding if auto
        #         create_plots: Whether to generate visualization plots
        #         top_n_plot: Number of top features to show in importance plots
        #         workspace_folder: The workspace folder to output files to | Optional
        #
        #     Returns:
        #         Dictionary with analysis results and file paths
        #     """,
        #     name="FeatureImportanceAnalysisTool"
        # )

        name = "feature_extraction_agent"
        # tools = [feature_extraction_tool_wrapper, feature_importance_tool_wrapper, *final_mcp_tools]
        tools = final_mcp_tools
        description = FEATURE_EXTRACTION_AGENT_DESCRIPTION
        system_message = FEATURE_EXTRACTION_AGENT_SYSTEM_PROMPT
        return cls(
            name = name,
            model_client = model_client,
            tools = tools,
            description = description,
            system_message = system_message,
            max_tool_iterations = 20
        )

    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        return [tool for tool in mcp_tools if tool.name in desired_tools]


async def main():
    from autogen_agentchat.ui import Console
    from autogen_core import CancellationToken
    
    # client = OllamaClient("qwen3-coder:480b-cloud")

    # Initialize the agent using the factory method
    # agent = await FeatureExtractionAgent.create(client)
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    SERVER_PATH = os.path.join(BASE_DIR, '..', '..', '..', 'mcp', 'server.py')
    workspace_dir = os.path.join(BASE_DIR, '..', '..', "workspace")

    agent = await FeatureExtractionAgent.create(workspace_folder=workspace_dir, mcp_server_path=SERVER_PATH)
    
    # Now you can use the agent normally
    print(f"Agent initialized with {agent._tools} tools.")
    print(agent._max_tool_iterations)
    for tool in agent._tools:
        print(tool.name)
    await Console(
        agent.run_stream(task="""Perform analysis, and find out the most important radiomics features for determining the health of the spleen. These are the inputs you'll need:
            {
                "hypothesis": "What are the most important features for determining the health of the spleen?",
                "image_dir": "/Users/ibrahim/Local Folder/GP/CUBE.AI/Backend/mas/memory/workspace/volumes_data",
                "mask_dir": "/Users/ibrahim/Local Folder/GP/CUBE.AI/Backend/mas/memory/workspace/labels_data",
                "target_csv": "/Users/ibrahim/Local Folder/GP/CUBE.AI/Backend/mas/memory/workspace/targets.csv",
                "target_column": "target",
                "subject_id_column": "subject_id",
            }
        """,
        cancellation_token=CancellationToken())
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
