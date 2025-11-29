import asyncio
import json
import uuid
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union, Dict
from pydantic import BaseModel, Field

# We will consider these types of messages (info: general logging - msg: chat witht the user  - plan: for the playground - hypothesis: for also the playground ))

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
from socket_store import send_ws_message, wait_for_user_message, close_ws

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
    state : Optional[str] = "not_confirmed"

class StudyStep(BaseModel):
    name: str
    description: str
    matched_module: Optional[str] = None
    status: str = "pending"

class StudyPlan(BaseModel):
    hypothesis_id: str
    study_goal: str
    steps: List[StudyStep] = Field(default_factory=list)
    state : Optional[str] = "not_confirmed"

# ============================== PLANNER ==============================

def clean_markdown_json(text: str) -> str:
    """Remove ```json ``` or ``` wrapping from LLM output."""
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```", "", text)
    text = re.sub(r"```$", "", text)
    return text

def format_plan(plan: StudyPlan) -> Dict:
    """
    Converts a StudyPlan object into the frontend schema with:
    - id: unique id (can use step index or UUID)
    - title: mapped from step.name
    - description: mapped from step.description
    """
    formatted_steps = []

    for idx, step in enumerate(plan.steps, start=1):
        formatted_steps.append({
            "id": f"s{idx}",          # or str(uuid4()) if you want unique ids
            "title": step.name,
            "description": step.description
        })

    return {"steps": formatted_steps}
    
class Planner:
    def __init__(
        self,
        config_path: str = "config.json",
        log_path: str = "system.log",
    ):
        self.config_path = Path(config_path)
        self.log_path = Path(log_path)
        self.state_path = Path(__file__).parent.parent / "memory" / "workspace" / "state.json"
        self.state =json.load(open(self.state_path)) if self.state_path.exists() else {}
        hypothesis = self.state.get('hypothesis')
        if hypothesis['id'] == '':
            self.hypothesis = ClinicalHypothesis(
                id="",
                title="",
                population="",
                intervention="",
                outcome="",
                clinical_context="",
                relationship_type="",
                state="not_confirmed"
            )
        else:
            self.hypothesis = ClinicalHypothesis(**hypothesis)
        
        print(self.hypothesis)
        plan = self.state.get('plan', StudyPlan(
            hypothesis_id= "",
            study_goal="",
            state = "not_confirmed"
        ))
# Validate and assign
        if isinstance(plan, dict):
            self.plan = StudyPlan(**plan)  # unpack dictionary
        elif isinstance(plan, StudyPlan):
            self.plan = plan 

        self.model_client = ollama_client
        self.status = "idle"
        self._initialize_logging()
        
        # Agents
        self.hypothesis_extractor = AssistantAgent(
            name="HypothesisExtractor",
            system_message=(
                "You are an expert biomedical researcher. "
                "You will be given a user query. Extract and structure a clinical hypothesis from the user query. "
                "Return valid JSON with fields: title, population, intervention, outcome, clinical_context, relationship_type, message."
                "in the message field explain to the user what you did and write your answer"
                "If it's not a hypothesis leave the fields empty and write in the message for the user to enter proper clinical hypothesis"
                "You must ensure the output is valid JSON."
                ""
            ),
            model_client=self.model_client,
        )

        self.hypothesis_explanator = AssistantAgent(
            name="HypothesisExplanator",
            system_message=(
                "You are an expert biomedical researcher. "
                "You will be given a user clinical hypothesis and a user query. Understand well the user query if it is a question or  modification. "
                "Answer the user question clearly or modify the hypothesis based on his request"
                "After modification Return valid JSON with fields: title, population, intervention, outcome, clinical_context, relationship_type, state, message ."
                "If there is no modification return the hypothesis without it change in your response"
                "in the message field explain to the user what you did and write your answer"
                "You must ensure the output is valid JSON."
                ""
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
        
        self.study_plan_explanator = AssistantAgent(
            name="StudyPlanner",
            system_message=(
                "You are a clinical researcher and have the current study plan."
                "based on the given hypothesis and the plan you should answer the questions of the user about any step and about the plan "
                "if the user asked to modify anything related to the plan, validate his modification and if it's clinically valid modify the plan" 
                "you should return a valid json and don't modify the plan structure as it's an ordered list of study steps. Each step must have a 'name' and 'description'."
                "add to the fields a message field contains explanation to the user what you did and write your answer"
                "return a valid json"
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
                
        self.capabilities_expert =  AssistantAgent(
            name="CapabilitiesExpert",
            system_message=(
                "You are an expert in program and modules capabilities. "
                "Given a study plan, and the module for each step"
                "and Given a descrption for every module. the user will ask questions related to modules and the plan"
                "answer the questions  of the user without changing the plan"
                "for additional modules or improvements."
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
            
    async def run(self, user_query: str):
        """Run the planning process with proper state management"""
        
        await send_ws_message("info", "Planner started")
        
        #intent classification
        if  self.hypothesis.id== "":
            await send_ws_message("info", "Processing your message...")
            message = await self.create_hypothesis(user_query)
            await send_ws_message("info", self.hypothesis.model_dump())
            
            await send_ws_message("msg", message)
            
             
        
        if self.hypothesis.state == "not_confirmed" and self.hypothesis.title :
            # format the the hypothesis and send it
            while self.hypothesis.state == "not_confirmed":
                await send_ws_message("msg", "Please confirm the hypothesis or ask me to modify or explain it")
            
                user_confirmation = await wait_for_user_message("user_confirmation")
                print(user_confirmation)
                await send_ws_message("info", "Processing your message...")
                
                message = await self.process_hypothesis(user_confirmation)
                print(self.hypothesis.state)
                
                await send_ws_message("hypothesis", self.hypothesis.model_dump())
                await send_ws_message("msg", message)
                    
            if self.hypothesis.state == "confirmed":
                self.save_to_config()                

            
        ##planning
        if self.plan.hypothesis_id == '':
            
            ## create plan
            await send_ws_message("msg", "Designing study plan from hypothesis...")
            message = await self.create_study_plan()
            await send_ws_message("plan", format_plan(self.plan.model_dump()))
            await send_ws_message("msg", message)

            # Match steps to available modules
            match_prompt = {
                "study_plan": self.plan.model_dump(),
                "available_modules": agents_description_for_prompt()
            }
            match_response = await self.steps_matcher.run(task=json.dumps(match_prompt, indent=2))
            match_content = clean_markdown_json(match_response.messages[-1].content)
            try:
                match_data = json.loads(match_content)
                # Expecting structure: {"study_plan": {"steps": [ ... ]}}
                matched_steps = match_data.get("study_plan", {}).get("steps", [])
                self.plan.steps = [StudyStep(**s) for s in matched_steps]
                await send_ws_message("modules_matched", {"steps": [s.model_dump() for s in self.plan.steps]})
            except Exception as e:
                raise ValueError(f"Failed to parse matched modules: {e}\nRaw: {match_content}")

            # Ask user to confirm / modify the plan until confirmed
            while self.plan.state == "not_confirmed":
                await send_ws_message("msg", "Please review and confirm the study plan or request modifications.")
                user_msg = await wait_for_user_message("plan_confirmation")
                print(user_msg)
                await send_ws_message("msg", "Processing your message!")
                msg = await self.process_study_plan(user_msg)
                print(msg)
                await send_ws_message("plan", format_plan(self.plan.model_dump()))
                await send_ws_message("msg", msg)

            if self.plan.state == "confirmed":
                self.save_to_config()                
        
        # self.status = "running"
        # self.save_to_config()
        
        # logger.info(f"Starting planning process for query: {user_query}")
        

        # # ---------------- Step 1: Hypothesis Extraction ----------------
        # logger.info("Extracting clinical hypothesis...")
        # hypo_response = await self.hypothesis_extractor.run(task=user_query)
        # hypo_content = clean_markdown_json(hypo_response.messages[-1].content)

        # try:
        #     hypo_data = json.loads(hypo_content)
        #     hypo_data["id"] = str(uuid.uuid4())
        #     self.hypothesis = ClinicalHypothesis(**hypo_data)
        # except Exception as e:
        #     raise ValueError(f"Failed to parse hypothesis output: {e}\nRaw: {hypo_content}")

        # print(f"Hypothesis structured:\n{json.dumps(self.hypothesis.model_dump(), indent=2)}\n")

        # # ---------------- Step 2: Study Plan ----------------
        # print("Designing study plan...")
        # plan_response = await self.study_planner.run(task=json.dumps(self.hypothesis.model_dump(), indent=2))
        # plan_content = clean_markdown_json(plan_response.messages[-1].content)

        # try:
        #     plan_data = json.loads(plan_content)
        #     self.plan.steps = [StudyStep(**s) for s in plan_data.get("steps", [])]
        # except Exception as e:
        #     raise ValueError(f"Failed to parse study plan output: {e}\nRaw: {plan_content}")

        # print(f"Study plan created with {len(self.plan.steps)} steps.\n")
        # # ---------------- Step 3: Module Matching ----------------
        # print("Matching study steps to available modules...")
        # match_prompt = {
        #     "study_plan": self.plan.model_dump(),
        #     "available_modules": agents_description_for_prompt()
        # }
        # match_response = await self.steps_matcher.run(task=json.dumps(match_prompt, indent=2))
        # match_content = clean_markdown_json(match_response.messages[-1].content)

        # try:
        #     match_data = json.loads(match_content)
            
        #     self.plan.steps = [StudyStep(**s) for s in match_data['study_plan']['steps']]
        #     print( match_data)
        # except Exception as e:
        #     raise ValueError(f"Failed to parse matched modules: {e}\nRaw: {match_content}")

        # print("Modules matched successfully.\n")

        # # ---------------- Step 4: Evaluation ----------------
        # print("Evaluating study plan quality...")
        # eval_response = await self.evaluator.run(task=json.dumps(self.plan.model_dump(), indent=2))
        # eval_content = eval_response.messages[-1].content

        # logger.info("Study plan evaluation complete")
        # self.status = "finished"
        # self.save_to_config()

        # result = {
        #     "hypothesis": self.hypothesis.model_dump(),
        #     "plan": self.plan.model_dump(),
        #     "evaluation": eval_content,
        #     "status": self.status
        # }

            
        return "result"

    async def create_hypothesis(self, user_query):
        task_str = f"this is a user query: {user_query}. Please validate that it is a proper clinical hypothesis and create a clinical hypothesis based on it. "
        logger.info("Extracting clinical hypothesis...")
        hypo_response = await self.hypothesis_extractor.run(task=task_str)
        hypo_content = clean_markdown_json(hypo_response.messages[-1].content)
        
        try:
            hypo_data = json.loads(hypo_content)
            
            hypo_data["id"] = str(uuid.uuid4())
            message = hypo_data.pop("message", "")

            self.hypothesis = ClinicalHypothesis(**hypo_data)
            self.state['hypothesis'] = self.hypothesis.model_dump()
        except Exception as e:
            raise ValueError(f"Failed to parse hypothesis output: {e}\nRaw: {hypo_content}")
        print(f"Hypothesis structured:\n{json.dumps(self.hypothesis.model_dump(), indent=2)}\n")
        
        return message
    
    async def process_hypothesis(self, user_query):
        if self.hypothesis.state == "not_confirmed":
            task_str = f"This is the user query {user_query} and this is the hypothesis {self.hypothesis.model_dump()}. If the user confirmed the hypothesis modify the state to be confirmed and if not yet ask for confirmation and keep it not_confirmed"
            logger.info("Processing clinical hypothesis...")

            response = await self.hypothesis_explanator.run(task=task_str)
            hypo_content = clean_markdown_json(response.messages[-1].content)
            try:
                hypo_data = json.loads(hypo_content)
                
                message = hypo_data.pop("message", "")
                hypo_data["id"] = self.hypothesis.id

                print(hypo_data)

                self.hypothesis = ClinicalHypothesis(**hypo_data)
                self.state['hypothesis'] = self.hypothesis.model_dump()
            except Exception as e:
                raise ValueError(f"Failed to parse hypothesis output: {e}\nRaw: {hypo_content}")
            print(f"Hypothesis structured:\n{json.dumps(self.hypothesis.model_dump(), indent=2)}\n")
            
            return message
    
    async def create_study_plan(self):
        """Create an initial study plan from the current hypothesis using the StudyPlanner agent."""
        logger.info("Designing study plan...")
        task_payload = json.dumps(self.hypothesis.model_dump(), indent=2)
        plan_response = await self.study_planner.run(task=task_payload)
        plan_content = clean_markdown_json(plan_response.messages[-1].content)

        try:
            plan_data = json.loads(plan_content)
            steps = plan_data.get("steps", [])
            self.plan = StudyPlan(
                hypothesis_id=self.hypothesis.id,
                study_goal=plan_data.get("study_goal", self.hypothesis.title or ""),
                steps=[StudyStep(**s) for s in steps],
                state=plan_data.get("state", "not_confirmed")
            )
            # ensure hypothesis linkage
            # self.plan.hypothesis_id = self.hypothesis.id
            self.state['plan'] = self.plan.model_dump()
            # self.save_to_config()
            return plan_data.get("message", "Study plan created.")
        except Exception as e:
            raise ValueError(f"Failed to parse study plan output: {e}\nRaw: {plan_content}")

    async def process_study_plan(self, user_query):
        """Process user feedback about the study plan (modify/confirm) using study_plan_explanator."""
        logger.info("Processing study plan feedback...")
        task_str = json.dumps({
            "user_query": user_query,
            "current_plan": self.plan.model_dump(),
            "hypothesis": self.hypothesis.model_dump()
        }, indent=2)
        response = await self.study_plan_explanator.run(task=task_str)
        content = clean_markdown_json(response.messages[-1].content)

        try:
            data = json.loads(content)
            # Example expected fields: steps (list), state, message
            steps = data.get("steps")
            if steps is not None:
                self.plan.steps = [StudyStep(**s) for s in steps]
            if "study_goal" in data:
                self.plan.study_goal = data["study_goal"]
            if "state" in data:
                self.plan.state = data["state"]
            # persist changes
            self.state['plan'] = self.plan.model_dump()
            self.save_to_config()
            return data.get("message", "Plan updated.")
        except Exception as e:
            raise ValueError(f"Failed to parse plan feedback: {e}\nRaw: {content}")
    
    def classify_intent(self, user_query: str) -> str:
        """Classify user query intent"""
        # we have three intents: "create_plan", "ask_step", "modify", "evaluate
        
        return "plan_study"
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
