import asyncio
import json
import uuid
import re
from typing import List, Optional, Union
from pydantic import BaseModel, Field

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.ollama import OllamaChatCompletionClient
import os

# ============================== CLIENT ==============================

ollama_client = OllamaChatCompletionClient(
    model="gpt-oss:120b-cloud",
    model_info={
        "vision": False,
        "function_calling": True,
        "json_output": True,
    },
    api_key="cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge"
)

# ============================== MODELS ==============================

class ClinicalHypothesis(BaseModel):
    id: str
    title: Optional[str] = ""
    population: Union[str, dict]
    intervention: Union[str, dict]
    outcome: Union[str, dict]
    clinical_context: Optional[str] = ""
    relationship_type: Optional[str] = ""

class StudyStep(BaseModel):
    name: str
    description: str
    matched_module: Optional[str] = None
    status: str = "pending"

class StudyPlan(BaseModel):
    hypothesis_id: str
    study_goal: str
    steps: List[StudyStep] = Field(default_factory=list)

# ============================== PLANNER ==============================

def clean_markdown_json(text: str) -> str:
    """Remove ```json ``` or ``` wrapping from LLM output."""
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```", "", text)
    text = re.sub(r"```$", "", text)
    return text

class Planner:
    def __init__(self, hypothesis: Optional[ClinicalHypothesis] = None):
        self.hypothesis = hypothesis or ClinicalHypothesis(
            id=str(uuid.uuid4()),
            population="",
            intervention="",
            outcome=""
        )
        self.plan = StudyPlan(
            hypothesis_id=self.hypothesis.id,
            study_goal=f"Investigate the hypothesis: {self.hypothesis.title}"
        )
        self.model_client = ollama_client

        # Agents
        self.hypothesis_extractor = AssistantAgent(
            name="HypothesisExtractor",
            system_message=(
                "You are an expert biomedical researcher. "
                "Extract and structure a clinical hypothesis from user input. "
                "Return valid JSON with fields: title, population, intervention, outcome, clinical_context, relationship_type."
            ),
            model_client=self.model_client,
        )

        self.study_planner = AssistantAgent(
            name="StudyPlanner",
            system_message=(
                "You are a clinical study planner. Design a detailed study plan "
                "based on the given hypothesis. Return JSON with a 'steps' key "
                "containing an ordered list of study steps. Each step must have a 'name' and 'description'."
            ),
            model_client=self.model_client,
        )

        self.steps_matcher = AssistantAgent(
            name="ModuleMatcher",
            system_message=(
                "You are a system integrator. Match each study plan step "
                "to one of the available modules. Return JSON with the same 'steps' array, "
                "but include a 'matched_module' key for each step."
            ),
            model_client=self.model_client,
        )

        self.evaluator = AssistantAgent(
            name="StudyEvaluator",
            system_message=(
                "You are a clinical research auditor. Assess the completeness, "
                "feasibility, and scientific soundness of the provided study plan. "
                "Return a concise textual evaluation with strengths and weaknesses."
            ),
            model_client=self.model_client,
        )

    async def run(self, user_query: str, tools_doc: str):
        self.status = "running"

        # ---------------- Step 1: Hypothesis Extraction ----------------
        print("\n[1️⃣] Extracting clinical hypothesis...")
        hypo_response = await self.hypothesis_extractor.run(task=user_query)
        hypo_content = clean_markdown_json(hypo_response.messages[-1].content)

        try:
            hypo_data = json.loads(hypo_content)
            hypo_data["id"] = str(uuid.uuid4())
            self.hypothesis = ClinicalHypothesis(**hypo_data)
        except Exception as e:
            raise ValueError(f"Failed to parse hypothesis output: {e}\nRaw: {hypo_content}")

        print(f"✅ Hypothesis structured:\n{json.dumps(self.hypothesis.model_dump(), indent=2)}\n")

        # ---------------- Step 2: Study Plan ----------------
        print("[2️⃣] Designing study plan...")
        plan_response = await self.study_planner.run(task=json.dumps(self.hypothesis.model_dump(), indent=2))
        plan_content = clean_markdown_json(plan_response.messages[-1].content)

        try:
            plan_data = json.loads(plan_content)
            self.plan.steps = [StudyStep(**s) for s in plan_data.get("steps", [])]
        except Exception as e:
            raise ValueError(f"Failed to parse study plan output: {e}\nRaw: {plan_content}")

        print(f"✅ Study plan created with {len(self.plan.steps)} steps.\n")
        # ---------------- Step 3: Module Matching ----------------
        print("[3️⃣] Matching study steps to available modules...")
        match_prompt = {
            "study_plan": self.plan.model_dump(),
            "available_modules":"""
                                - PopulationSelector: defines patient inclusion/exclusion.
                                - ImagingProtocolDesigner: designs imaging acquisition settings.
                                - SegmentationModule: automates ROI segmentation.
                                - RadiomicsExtractor: extracts features.
                                - ModelingModule: trains ML models for prediction.
                                - ValidationModule: performs internal/external validation.
                            """
        }
        match_response = await self.steps_matcher.run(task=json.dumps(match_prompt, indent=2))
        match_content = clean_markdown_json(match_response.messages[-1].content)

        try:
            match_data = json.loads(match_content)
            
            self.plan.steps = [StudyStep(**s) for s in match_data['study_plan']['steps']]
            print( match_data)
        except Exception as e:
            raise ValueError(f"Failed to parse matched modules: {e}\nRaw: {match_content}")

        print("✅ Modules matched successfully.\n")

        # ---------------- Step 4: Evaluation ----------------
        print("[4️⃣] Evaluating study plan quality...")
        eval_response = await self.evaluator.run(task=json.dumps(self.plan.model_dump(), indent=2))
        eval_content = eval_response.messages[-1].content

        print("✅ Study plan evaluation complete.\n")
        self.status = "finished"

        return {
            "hypothesis": self.hypothesis.model_dump(),
            "plan": self.plan.model_dump(),
            "evaluation": eval_content
        }

# ============================== MAIN ==============================

async def main():
    planner = Planner()
    user_query = "A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma."
    tools_doc = """
    - PopulationSelector: defines patient inclusion/exclusion.
    - ImagingProtocolDesigner: designs imaging acquisition settings.
    - SegmentationModule: automates ROI segmentation.
    - RadiomicsExtractor: extracts features.
    - ModelingModule: trains ML models for prediction.
    - ValidationModule: performs internal/external validation.
    """
    result = await planner.run(user_query, tools_doc)
    print("\n🎯 Final Output:")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
