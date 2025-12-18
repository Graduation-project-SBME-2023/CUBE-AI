import asyncio
import sys
import os
import logging
from datetime import datetime

def setup_logging():
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(log_dir, f"execution_{timestamp}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="w", encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ],
        force=True,
    )

    logging.info(f"Logging initialized: {log_file}")
    return log_file

setup_logging()

# Optional: Silence overly verbose libraries if needed (e.g., http requests)
# logging.getLogger("httpx").setLevel(logging.WARNING)

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(root_dir)

import asyncio
from autogen_agentchat.agents import AssistantAgent, MessageFilterAgent, MessageFilterConfig, PerSourceFilter
from autogen_agentchat.teams import DiGraphBuilder, GraphFlow
from autogen_agentchat.ui import Console
from clients.ollamaClient import OllamaClient

# Import your existing custom agent classes
from agents.execution_agents.segmentation_agent import SegmentationAgent
from agents.execution_agents.feature_extraction_agent import FeatureExtractionAgent
from agents.execution_agents.modeling_agent import ModelingAgent
from agents.execution_agents.result_reporter_agent import ResultReporterAgent
from agents.execution_agents.reporter_agent import ReporterAgent

async def main() -> None:
    # 1. Initialize the raw agents
    # seg_agent = await SegmentationAgent.create()
    feat_agent = await FeatureExtractionAgent.create()
    model_agent = await ModelingAgent.create()
    report_agent = await ResultReporterAgent.create()
    latex_agent = await ReporterAgent.create()

    filtered_feat_agent = MessageFilterAgent(
        name="feature_extraction_agent",
        wrapped_agent=feat_agent,
        filter=MessageFilterConfig(per_source=[
            PerSourceFilter(source="segmentation_agent", position="last", count=1)
        ])
    )

    filtered_model_agent = MessageFilterAgent(
        name="modeling_agent",
        wrapped_agent=model_agent,
        filter=MessageFilterConfig(per_source=[
            PerSourceFilter(source="feature_extraction_agent", position="last", count=1)
        ])
    )

    filtered_report_agent = MessageFilterAgent(
        name="result_reporter_agent",
        wrapped_agent=report_agent,
        filter=MessageFilterConfig(per_source=[
            PerSourceFilter(source="user", position="first", count=1),
            PerSourceFilter(source="modeling_agent", position="last", count=1)
        ])
    )
    
    filtered_latex_agent = MessageFilterAgent(
        name = "latex_reporter_agent",
        wrapped_agent=latex_agent,
        filter=MessageFilterConfig(per_source=[
            PerSourceFilter(source = "result_reporter_agent", position="last", count=1)
        ]
            
        )
    )

    builder = DiGraphBuilder()
    # builder.add_node(seg_agent)
    builder.add_node(feat_agent)
    builder.add_node(filtered_model_agent)
    builder.add_node(filtered_report_agent)
    builder.add_node(filtered_latex_agent)


    # builder.add_edge(seg_agent, filtered_feat_agent)
    
    builder.add_edge(feat_agent, filtered_model_agent)
    
    builder.add_edge(filtered_model_agent, filtered_report_agent)
    
    builder.add_edge(filtered_report_agent, filtered_latex_agent)


    flow = GraphFlow(
        participants=builder.get_participants(),
        graph=builder.build()
    )

    task_prompt = """Check what Radiomic features best predict the health of the spleen. The data is already downloaded and ready in the following manner.
                "image_dir": "/Users/ibrahim/Local Folder/GP/CUBE.AI/Backend/mas/memory/workspace/volumes_data",
                "mask_dir": "/Users/ibrahim/Local Folder/GP/CUBE.AI/Backend/mas/memory/workspace/labels_data",
                "target_csv": "/Users/ibrahim/Local Folder/GP/CUBE.AI/Backend/mas/memory/workspace/targets.csv",
                "target_label_name": "target"""

    print("--- Starting GraphFlow Pipeline ---")
    await Console(flow.run_stream(task=task_prompt))

if __name__ == "__main__":
    asyncio.run(main())



# task_prompt = """start the radiomics pipeline
#                 "image_dir": "C:/sbme 26/GP/refactored project/OHIF-Standalone/Backend/mas/memory/workspace/labels_data",
#                 "mask_dir": "C:/sbme 26/GP/refactored project/OHIF-Standalone/Backend/mas/memory/workspace/labels_data",
#                 "target_csv": "C:/sbme 26/GP/refactored project/OHIF-Standalone/Backend/mas/memory/workspace/targets.csv",
#                 "target_label_name": "target"
#                 """
