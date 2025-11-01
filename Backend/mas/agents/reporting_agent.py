"""
ReportingAgent – generates human-readable reports.

Consumes the final results (hypothesis, plan, evaluation, predictions)
and produces a structured narrative JSON summary that includes:
- Summaries of CSV files (features, model results)
- Hypothesis and plan summary
- Dataset overview
- Decision (accept/reject hypothesis)
"""

from typing import Dict, Any, Optional
from pathlib import Path
import json
import pandas as pd
import logging
import asyncio

#from autogen_ext.models.ollama import OllamaChatCompletionClient
import asyncio
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from autogen_ext.models.ollama import OllamaChatCompletionClient


class ReportingAgent:
    """Extended reporting agent that summarizes and evaluates pipeline outputs."""

    def __init__(self, config_path: str = "config.json", model_name: str = "qwen2.5-32b-instruct"):
        self.config_path = config_path
        self.logger = logging.getLogger(self.__class__.__name__)

        # LLM client for reasoning and decision generation
        self.model_client = OllamaChatCompletionClient(
            model=model_name,
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": True,
                "structured_output": True,
            }
        )

        # Specialized report generation agent
        self.reporting_agent = AssistantAgent(
            name="ReportingAssistant",
            system_message=(
                "You are a scientific reporting assistant. "
                "Your goal is to generate a structured, scientific-style summary report "
                "based on study hypothesis, plan, results, and data analysis.\n\n"
                "Carefully read the inputs: hypothesis, plan steps, dataset info, important features, and model results. "
                "Then decide whether the hypothesis should be accepted or rejected, and justify your decision.\n\n"
                "Your response MUST be valid JSON in the format:\n"
                "{\n"
                '  "status": "success",\n'
                '  "message": "Report generated successfully",\n'
                '  "report": {\n'
                '      "summary": "<scientific summary>",\n'
                '      "decision": "accepted" | "rejected",\n'
                '      "justification": "<short explanation>",\n'
                '      "markdown": "<formatted markdown version of the report>",\n'
                '      "phase": "reporting"\n'
                '  }\n'
                "}\n\n"
                "If something goes wrong, respond with:\n"
                "{ 'status': 'failed', 'message': 'Error explanation' }"
            ),
            model_client=self.model_client,
        )

    async def initialize(self) -> bool:
        """Stub for loading templates or preparing the environment."""
        return True

    async def _read_csv_summary(self, csv_path: Optional[str], n_rows: int = 5) -> str:
        """Summarize a CSV file by showing its head and describing numeric stats."""
        if not csv_path or not Path(csv_path).exists():
            return f"File not found: {csv_path}"

        try:
            df = pd.read_csv(csv_path)
            head = df.head(n_rows).to_dict(orient="records")
            stats = df.describe(include="all").to_dict()
            return json.dumps({
                "path": csv_path,
                "preview_rows": head,
                "statistics": stats
            }, indent=2)
        except Exception as e:
            return f"Error reading {csv_path}: {str(e)}"

    async def run(self, result_bundle: Dict[str, Any]) -> str:
        """
        Generates a structured report using CSV data and metadata.
        Saves the final JSON report to disk.
        """        
        current_dir = Path(__file__).resolve().parent
        state_path = current_dir.parent / "memory" / "workspace" / "stat.json"
        config_path = current_dir.parent / "memory" / "config.json"
        top_features_path = current_dir.parent / "memory" / "workspace" / "top_10_features.csv"
        model_results_path = current_dir.parent / "memory" / "workspace" / "models" / "tuned_model_1"/ "tuned_model_1_results.csv"
        # Backend\mas\memory\workspace\models\tuned_model_1\tuned_model_1_results.csv
        # Backend\mas\memory\workspace\models\tuned_model_1\independent_eval_results_tuned_model_1.csv
        model_val_path = current_dir.parent / "memory" / "workspace" / "models" / "tuned_model_1"/ "independent_eval_results_tuned_model_1.csv"
        try:
            
            
            self.logger.info("Starting ReportingAgent...")
            hypothesis = result_bundle.get("hypothesis", "N/A")
            plan = result_bundle.get("plan", {}).get("steps", [])
            dataset_info = result_bundle.get("dataset_info", {})
            important_features_csv = result_bundle.get("important_features_csv")
            model_results_csv = result_bundle.get("model_results_csv")

            # Summarize CSVs
            important_features_summary = await self._read_csv_summary(important_features_csv)
            model_results_summary = await self._read_csv_summary(model_results_csv)

            # Construct reporting prompt
            prompt = (
                f"=== STUDY HYPOTHESIS ===\n{hypothesis}\n\n"
                f"=== STUDY PLAN ===\n{json.dumps(plan, indent=2)}\n\n"
                f"=== DATASET INFO ===\n{json.dumps(dataset_info, indent=2)}\n\n"
                f"=== IMPORTANT FEATURES SUMMARY ===\n{important_features_summary}\n\n"
                f"=== MODEL RESULTS SUMMARY ===\n{model_results_summary}\n\n"
                "Please generate a concise scientific report and decision."
            )

            # Generate final structured report from LLM
            report_response = await self.reporting_agent.run(task=prompt)

            # Convert to Python dict
            if isinstance(report_response, str):
                report_data = json.loads(report_response)
            else:
                report_data = report_response

            # Save the report
            output_path = Path(self.config_path).with_name("final_report.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=2)

            self.logger.info(f"Report saved at {output_path}")
            return json.dumps({"status": "success", "message": "Report generated and saved.", "path": str(output_path)}, indent=2)

        except Exception as e:
            self.logger.error(f"ReportingAgent error: {e}", exc_info=True)
            return json.dumps({"status": "failed", "message": str(e)}, indent=2)


__all__ = ["ReportingAgent"]
