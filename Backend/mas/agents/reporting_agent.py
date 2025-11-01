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
import re

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.ollama import OllamaChatCompletionClient

def clean_markdown_json(text: str) -> str:
    """Remove ```json ``` or ``` wrapping from LLM output."""
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```", "", text)
    text = re.sub(r"```$", "", text)
    return text
class ReportingAgent:
    """Extended reporting agent that summarizes and evaluates pipeline outputs."""

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

        # LLM client for reasoning and decision generation
        self.model_client = OllamaChatCompletionClient(
                model="gpt-oss:120b-cloud",
                model_info={
                    "vision": False,
                    "function_calling": True,
                    "json_output": True
                },
                api_key="cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge"
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

    def _load_json(self, path: Path) -> Optional[dict]:
        """Safely load a JSON file."""
        if not path.exists():
            self.logger.warning(f"Missing JSON file: {path}")
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Error reading JSON {path}: {e}")
            return None

    def _load_csv(self, path: Path) -> Optional[pd.DataFrame]:
        """Safely load a CSV file."""
        if not path.exists():
            self.logger.warning(f"Missing CSV file: {path}")
            return None
        try:
            return pd.read_csv(path)
        except Exception as e:
            self.logger.error(f"Error reading CSV {path}: {e}")
            return None

    async def _summarize_csv(self, df: Optional[pd.DataFrame], name: str, n_rows: int = 5) -> str:
        """Return a summary (head + stats) of a CSV as a string."""
        if df is None or df.empty:
            return f"No data found for {name}."
        try:
            head = df.head(n_rows).to_dict(orient="records")
            stats = df.describe(include="all").to_dict()
            return json.dumps({"name": name, "preview_rows": head, "statistics": stats}, indent=2)
        except Exception as e:
            return f"Error summarizing {name}: {e}"

    async def run(self, result_bundle: Dict[str, Any]) -> str:
        """
        Generates a structured report using CSV data and metadata.
        Saves the final JSON report to disk.
        """
        try:
            self.logger.info("Starting ReportingAgent...")

            # === Define all paths ===
            current_dir = Path(__file__).resolve().parent
            state_path = current_dir.parent / "memory" / "workspace" / "state.json"
            config_path = current_dir.parent / "memory" / "config.json"
            top_features_path = current_dir.parent / "memory" / "workspace" / "top_10_features.csv"
            model_results_path = current_dir.parent / "memory" / "workspace" / "models" / "tuned_model_1" / "tuned_model_1_results.csv"
            model_val_path = current_dir.parent / "memory" / "workspace" / "models" / "tuned_model_1" / "independent_eval_results_tuned_model_1.csv"

            # === Load data ===
            state_data = self._load_json(state_path)
            config_data = self._load_json(config_path)
            features_df = self._load_csv(top_features_path)
            model_results_df = self._load_csv(model_results_path)
            val_results_df = self._load_csv(model_val_path)

            # === Summarize data ===
            features_summary = await self._summarize_csv(features_df, "Top Features")
            model_results_summary = await self._summarize_csv(model_results_df, "Model Results")
            val_results_summary = await self._summarize_csv(val_results_df, "Independent Evaluation")

            # === Extract essential metadata ===
            hypothesis = (state_data or {}).get("hypothesis", {})
            plan = (state_data or {}).get("plan", {}).get("steps", [])
            dataset_info = (config_data or {}).get("dataset", {})

            # === Build the report generation prompt ===
            prompt = (
                f"=== STUDY HYPOTHESIS ===\n{json.dumps(hypothesis, indent=2)}\n\n"
                f"=== STUDY PLAN ===\n{json.dumps(plan, indent=2)}\n\n"
                f"=== DATASET INFO ===\n{json.dumps(dataset_info, indent=2)}\n\n"
                f"=== IMPORTANT FEATURES SUMMARY ===\n{features_summary}\n\n"
                f"=== MODEL RESULTS SUMMARY ===\n{model_results_summary}\n\n"
                f"=== VALIDATION RESULTS SUMMARY ===\n{val_results_summary}\n\n"
                "Please generate a concise scientific report and decide whether the hypothesis should be accepted or rejected."
            )

            # === Generate structured report ===
            report_response = await self.reporting_agent.run(task=prompt)
            report_response = clean_markdown_json(report_response.messages[-1].content)
            # === Parse LLM response ===
            if isinstance(report_response, str):
                report_data = json.loads(report_response)
            else:
                report_data = report_response

            # === Save report ===
            output_path = current_dir.parent / "memory" / "workspace" / "final_report.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=2)

            self.logger.info(f"Report saved at {output_path}")
            return json.dumps({
                "status": "success",
                "message": "Report generated and saved successfully.",
                "path": str(output_path)
            }, indent=2)

        except Exception as e:
            self.logger.error(f"ReportingAgent error: {e}", exc_info=True)
            return json.dumps({
                "status": "failed",
                "message": str(e)
            }, indent=2)


# === Run test simulation ===
if __name__ == "__main__":
    async def main():
        agent = ReportingAgent()
        dummy_result_bundle = {}
        result = await agent.run(dummy_result_bundle)
        print(result)

    asyncio.run(main())
