'''
Planner works initialy with the userinput handler

It validates the argument
Define the hypothesis
Set the needed steps
Run the steps in sequence

'''


from dataclasses import dataclass, field
from typing import List, Literal, Optional
import uuid



@dataclass
class ClinicalHypothesis:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    population_name: str = ""
    population_attributes: List[str] = field(default_factory=list)
    intervention_name: str = ""
    intervention_type: str = ""
    intervention_details: str = ""
    outcome_name: str = ""
    outcome_measurement: str = ""
    clinical_field: str = ""
    clinical_condition: str = ""
    relationship_type: Literal["prediction", "diagnosis", "prognosis", "association", "causal"] = "prediction"
    source_query: str = ""
    status: Literal["draft", "validated", "processing", "completed"] = "draft"

@dataclass
class UserInputRequest:
    field : str = ""
    description : str = ""

@dataclass
class UserInputResponse:
    status: str = ""
    message: str = ""
    data: Optional[UserInputRequest] = None

@dataclass
class StudyStep:
    name: str
    description: str
    status: str = "pending"  # pending, running, completed, waiting_input
    assigned_module: str = ""

@dataclass
class StudyPlan:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    hypothesis_id: str = ""
    study_goal: str = ""
    steps: List[StudyStep] = field(default_factory=list)
    

class Planner:
    def __init__(self, hypothesis: ClinicalHypothesis):
        self.hypothesis = hypothesis
        self.plan = StudyPlan(hypothesis_id=hypothesis.id, study_goal=f"Investigate the hypothesis: {hypothesis.title}")

    
    def run(self):
        # READ STATUES
        # EXTRACT HYPOTHESIS DETAILS
        # vALIDATE HYPOTHESIS
        # DEFINE STUDY STEPS
        # MATCH STEPS TO MODULES

        
        return "Planning completed"
    
    def define_study_steps(self):
        # Example steps based on hypothesis
        self.plan.steps.append(StudyStep(
            name="Literature Review",
            description="Conduct a comprehensive literature review on the clinical condition and related studies."
        ))
        self.plan.steps.append(StudyStep(
            name="Data Collection",
            description="Gather relevant clinical and imaging data for the defined population."
        ))
        self.plan.steps.append(StudyStep(
            name="Data Preprocessing",
            description="Preprocess the collected data to ensure quality and consistency."
        ))
        self.plan.steps.append(StudyStep(
            name="Statistical Analysis",
            description="Perform statistical analysis to evaluate the relationship between intervention and outcome."
        ))
        self.plan.steps.append(StudyStep(
            name="Report Findings",
            description="Compile and report the findings of the study."
        ))

    def get_plan(self) -> StudyPlan:
        return self.plan


# === 1. Hypothesis Extraction Agent ===
hypothesis_agent = AssistantAgent(
    name="HypothesisExtractor",
    system_message="You are an expert biomedical researcher. Extract and structure a clinical hypothesis from user input following the ClinicalHypothesis schema.",
)

# === 2. Study Planner Agent ===
planner_agent = AssistantAgent(
    name="StudyPlanner",
    system_message="You are a clinical study planner. Design a structured study plan based on the provided clinical hypothesis using research methodology best practices.",
)

# === 3. Module Matching Agent ===
matcher_agent = AssistantAgent(
    name="ModuleMatcher",
    system_message="You are a system integrator. Match each step in the study plan to an available system module using semantic similarity. Return the updated plan.",
)
# === 4. User Proxy (simulated user) ===
user_agent = UserProxyAgent(
    name="User",
    human_input_mode="NEVER"  # or 'ALWAYS' if you want manual confirmation
)


user_query = "A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma."



# === Orchestrate ===
user_query = "A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma."

# Step 1: Extract hypothesis
hypothesis = hypothesis_agent.generate_reply(messages=[{"role": "user", "content": user_query}])
print("\n[Hypothesis Extracted]\n", hypothesis)

# Step 2: Generate study plan
plan = planner_agent.generate_reply(messages=[{"role": "user", "content": f"Hypothesis: {hypothesis}"}])
print("\n[Study Plan Created]\n", plan)

# Step 3: Match plan with modules
matcher_input = f"Study plan: {plan}\nAvailable modules:\n{json.dumps(MODULES_METADATA, indent=2)}"
matched_plan = matcher_agent.generate_reply(messages=[{"role": "user", "content": matcher_input}])
print("\n[Matched Study Plan]\n", matched_plan)

# Optionally save the plan
Path("project_manager/core/plans/plan_output.json").write_text(matched_plan)