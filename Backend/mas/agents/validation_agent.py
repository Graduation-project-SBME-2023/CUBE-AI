"""
ValidationModule – performs internal / external validation of the study.

It can compute performance metrics (AUC, accuracy, etc.) on a held‑out
cohort, or run cross‑validation, and returns a concise evaluation.
"""

from autogen_ext.models.ollama import OllamaChatCompletionClient
import asyncio
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.tools.langchain import LangChainToolAdapter
from autogen_core import CancellationToken
from pydantic import BaseModel
from typing import List, Tuple, Union, Dict, Any
from typing import Dict, Any
import pandas as pd
import requests
import datetime
import json
import asyncio

from pathlib import Path

def get_results_files():
    """
    this function returns the comparison of the trained machine learning models anf the main radiomics features importance 
    """
    # Get current directory of this file
    current_dir = Path(__file__).resolve().parent

    # Go one level up (parent folder), then into memory/workspace/
    workspace_dir = current_dir.parent / "memory" / "workspace"

    model_comparison = pd.read_csv(workspace_dir / "model_comparison_results.csv")
    features = pd.read_csv(workspace_dir / "feature_importance.csv")
    return features, model_comparison



class ValidationModule:
    """Placeholder for the validation module."""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        
        self.model_client = OllamaChatCompletionClient(
        model="gpt-oss:120b-cloud",
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "structured_output": True
        }
    )

    async def initialize(self) -> bool:
        """Prepare any validation datasets (stub)."""
        return True

    async def run(self):
        """
        Compute validation metrics.

        Returns a dict such as ``{"auc": 0.85, "accuracy": 0.78}``.
        """
        # Stub: return empty metrics
        agent = AssistantAgent(
        name="radiologist",
        model_client=self.model_client,
        tools=[get_results_files],
        max_tool_iterations= 5,
        system_message="""you are a validation agent, you are a part of a research paper making process,
        you will be given a hypothesis and the results from the experiment of this hypothesis, and then you need to 
        validate that hypothesis and what is the conclusion of this hypothesis
        """,
        )

    # # Let the agent translate some text
        await Console(
        agent.run_stream(task="A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma",
                         cancellation_token=CancellationToken())
        )
        
        
        
        
        return {}


__all__ = ["ValidationModule"]


async def main():
    pop = ValidationModule()
    await pop.run()  # also needs await if run() is async

asyncio.run(main())