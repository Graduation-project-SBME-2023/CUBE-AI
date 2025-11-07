import asyncio
import json
import uuid
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.ollama import OllamaChatCompletionClient
import os

from agent_registry import agents_description_for_prompt

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
    def __init__(
        self,
        config_path: str = "config.json",
        log_path: str = "system.log",
        hypothesis: Optional[ClinicalHypothesis] = None
    ):
        self.config_path = Path(config_path)
        self.log_path = Path(log_path)
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
        self.status = "idle"
        self._initialize_logging()
        
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


    def _initialize_logging(self):
        """Set up file logging"""
        logger = logging.getLogger(__name__)
        file_handler = logging.FileHandler(self.log_path)
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )
        logger.addHandler(file_handler)
        
    def save_to_config(self):
        """Save current plan and hypothesis to config file"""
        try:
            current_dir = Path(__file__).resolve().parent
            json_path = current_dir.parent / "memory" / "workspace" / "state.json"
            # Save the complete result to history
            
            with open(json_path, "r", encoding="utf-8") as f:
                state = json.load(f)

                state['plan'] = self.plan.model_dump()
                state['hypothesis'] = self.hypothesis.model_dump()
                state['planner_status'] = self.status
                
                with open(json_path, 'w') as f:
                    json.dump(state, f, indent=2)
                    
            logger.info("Saved plan and hypothesis to state file")
        except Exception as e:
            logger.error(f"Failed to save to config: {e}")
            
    def load_from_config(self):
        """Load saved plan and hypothesis from config"""
        try:
            if self.config_path.exists():
                with open(self.config_path) as f:
                    config = json.load(f)
                    if 'current_plan' in config:
                        self.plan = StudyPlan(**config['current_plan'])
                    if 'current_hypothesis' in config:
                        self.hypothesis = ClinicalHypothesis(**config['current_hypothesis'])
                    self.status = config.get('planner_status', 'idle')
                    logger.info("Loaded plan and hypothesis from config file")
        except Exception as e:
            logger.error(f"Failed to load from config: {e}")
            
    async def request_user_input(self, description: str) -> str:
        """Request user input during planning"""
        self.status = "waiting_input"
        self.save_to_config()
        # This will be handled by the orchestrator and user input handler
        return ""

    async def run(self, user_query: str):
        """Run the planning process with proper state management"""
        self.status = "running"
        self.save_to_config()
        
        logger.info(f"Starting planning process for query: {user_query}")

        # ---------------- Step 1: Hypothesis Extraction ----------------
        logger.info("Extracting clinical hypothesis...")
        hypo_response = await self.hypothesis_extractor.run(task=user_query)
        hypo_content = clean_markdown_json(hypo_response.messages[-1].content)

        try:
            hypo_data = json.loads(hypo_content)
            hypo_data["id"] = str(uuid.uuid4())
            self.hypothesis = ClinicalHypothesis(**hypo_data)
        except Exception as e:
            raise ValueError(f"Failed to parse hypothesis output: {e}\nRaw: {hypo_content}")

        print(f"Hypothesis structured:\n{json.dumps(self.hypothesis.model_dump(), indent=2)}\n")

        # ---------------- Step 2: Study Plan ----------------
        print("Designing study plan...")
        plan_response = await self.study_planner.run(task=json.dumps(self.hypothesis.model_dump(), indent=2))
        plan_content = clean_markdown_json(plan_response.messages[-1].content)

        try:
            plan_data = json.loads(plan_content)
            self.plan.steps = [StudyStep(**s) for s in plan_data.get("steps", [])]
        except Exception as e:
            raise ValueError(f"Failed to parse study plan output: {e}\nRaw: {plan_content}")

        print(f"Study plan created with {len(self.plan.steps)} steps.\n")
        # ---------------- Step 3: Module Matching ----------------
        print("Matching study steps to available modules...")
        match_prompt = {
            "study_plan": self.plan.model_dump(),
            "available_modules": agents_description_for_prompt()
        }
        match_response = await self.steps_matcher.run(task=json.dumps(match_prompt, indent=2))
        match_content = clean_markdown_json(match_response.messages[-1].content)

        try:
            match_data = json.loads(match_content)
            
            self.plan.steps = [StudyStep(**s) for s in match_data['study_plan']['steps']]
            print( match_data)
        except Exception as e:
            raise ValueError(f"Failed to parse matched modules: {e}\nRaw: {match_content}")

        print("Modules matched successfully.\n")

        # ---------------- Step 4: Evaluation ----------------
        print("Evaluating study plan quality...")
        eval_response = await self.evaluator.run(task=json.dumps(self.plan.model_dump(), indent=2))
        eval_content = eval_response.messages[-1].content

        logger.info("Study plan evaluation complete")
        self.status = "finished"
        self.save_to_config()

        result = {
            "hypothesis": self.hypothesis.model_dump(),
            "plan": self.plan.model_dump(),
            "evaluation": eval_content,
            "status": self.status
        }

            
        return result

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
    result = await planner.run(user_query)
    print("\nFinal Output:")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
