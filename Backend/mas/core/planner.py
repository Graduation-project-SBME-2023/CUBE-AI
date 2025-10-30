import asyncio
import json
import uuid
from pydantic import BaseModel, Field
from typing import List, Optional
from autogen import AssistantAgent, LLMConfig
from autogen.agentchat import chat
from autogen_ext.models.ollama import OllamaChatCompletionClient

from autogen_ext.models.openai import OpenAIChatCompletionClient  
import os
# Create the shared LLM config once


ollama_client = OllamaChatCompletionClient(
            model="gpt-oss:latest",
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": True,
            },
            base_url="http://localhost:11434/v1" , # Ollama API endpoint
        
            api_key = "cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge" 
    )  
config_list =     {
        "model": "gpt-oss:120b",
        "api_type": "ollama",
        "api_key": os.getenv("OLLAMA_API_KEY"),
        "base_url": "https://ollama.com"
    }



llm_config = LLMConfig(
    {
        "model": "llama3.1:latest",       # required field!
        "client": ollama_client,          # custom Ollama client object
        "api_type": "ollama",             # label to skip openai handling
        "base_url": "http://localhost:11434",  # optional if Ollama runs locally
    },
    temperature=0.2,
)

# ============================== MODELS ==============================

class ClinicalHypothesis(BaseModel):
    id: str
    title: str
    population: str
    intervention: str
    outcome: str
    clinical_context: str
    relationship_type: str


class StudyStep(BaseModel):
    name: str
    description: str
    matched_module: Optional[str] = None
    status: str = "pending"  # pending, running, done


class StudyPlan(BaseModel):
    hypothesis_id: str
    study_goal: str
    steps: List[StudyStep] = Field(default_factory=list)


# ============================== PLANNER ==============================

class Planner:
    def __init__(self, hypothesis: Optional[ClinicalHypothesis] = None):
        self.hypothesis = hypothesis or ClinicalHypothesis(
            id=str(uuid.uuid4()),
            title="",
            population="",
            intervention="",
            outcome="",
            clinical_context="",
            relationship_type=""
        )

        self.plan = StudyPlan(
            hypothesis_id=self.hypothesis.id,
            study_goal=f"Investigate the hypothesis: {self.hypothesis.title}"
        )
        self.status = "idle"

        # LLM client
        self.model_client =  config_list,


        # Agents
        self.hypothesis_extractor = AssistantAgent(
            name="HypothesisExtractor",
            system_message=(
                "You are an expert biomedical researcher. "
                "Extract and structure a clinical hypothesis from user input "
                "and return a valid JSON matching the ClinicalHypothesis schema."
            ),
            llm_config=self.model_client,
        )

        self.study_planner = AssistantAgent(
            name="StudyPlanner",
            system_message=(
                "You are a clinical study planner. Design a structured study plan "
                "based on the provided clinical hypothesis. "
                "Return a JSON object with a 'steps' key containing step descriptions."
            ),
            llm_config=self.model_client,
        )

        self.steps_matcher = AssistantAgent(
            name="ModuleMatcher",
            system_message=(
                "You are a system integrator. Match each study plan step "
                "to one available module using semantic similarity. "
                "Return the updated plan in JSON with matched_module fields."
            ),
            llm_config=self.model_client,
        )

        self.evaluator = AssistantAgent(
            name="StudyEvaluator",
            system_message=(
                "You are a study quality evaluator. "
                "Assess whether the study plan is complete, feasible, and methodologically sound."
            ),
            llm_config=self.model_client,
        )

    # ----------------------- MAIN ORCHESTRATION -----------------------

    async def run(self, user_query: str, tools_doc: str):
        self.status = "running"

        print("\n[1️⃣] Extracting clinical hypothesis...")
        hypo_response =  self.hypothesis_extractor.run(user_query)
        hypo_content = hypo_response.messages

        try:
            hypo_data = json.loads(hypo_content)
            self.hypothesis = ClinicalHypothesis(**hypo_data)
        except Exception as e:
            raise ValueError(f"Failed to parse hypothesis output: {e}\nRaw: {hypo_content}")

        print(f"✅ Hypothesis structured: {self.hypothesis.title}\n")

        print("[2️⃣] Designing study plan...")
        plan_response = await self.study_planner.run(self.hypothesis.dict())
        plan_content = plan_response.output_text
        try:
            plan_data = json.loads(plan_content)
            self.plan.steps = [StudyStep(**s) for s in plan_data.get("steps", [])]
        except Exception as e:
            raise ValueError(f"Failed to parse study plan output: {e}\nRaw: {plan_content}")

        print(f"✅ Study plan created with {len(self.plan.steps)} steps.\n")

        print("[3️⃣] Matching modules...")
        match_response = await self.steps_matcher.run({
            "study_plan": self.plan.dict(),
            "available_modules": tools_doc
        })
        match_content = match_response.output_text
        try:
            match_data = json.loads(match_content)
            self.plan.steps = [StudyStep(**s) for s in match_data.get("steps", [])]
        except Exception as e:
            raise ValueError(f"Failed to parse module matching output: {e}\nRaw: {match_content}")

        print("✅ Modules matched successfully.\n")

        print("[4️⃣] Evaluating study plan...")
        eval_response = await self.evaluator.run(self.plan.dict())
        eval_content = eval_response.output_text

        self.status = "finished"
        print("✅ Study plan evaluation complete.\n")

        return {
            "plan": self.plan.dict(),
            "evaluation": eval_content
        }

    def get_status(self):
        return self.status

    def get_plan(self):
        return self.plan


# ============================== MAIN EXECUTION ==============================

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
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
