"""
Executer manages the execution of study plan modules in sequence.
It handles module initialization, state management, and user input coordination.
"""

import asyncio
import json
import logging
from enum import Enum
from typing import Dict, List, Optional, Any
from pathlib import Path
from pydantic import BaseModel, Field

from planner import StudyPlan, StudyStep

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class StudyStep(BaseModel):
    name: str
    description: str
    matched_module: Optional[str] = None
    status: str = "pending"

class StudyPlan(BaseModel):
    hypothesis_id: str
    study_goal: str
    steps: List[StudyStep] = Field(default_factory=list)


class ModuleStatus(str, Enum):
    """Status states for module execution"""
    PENDING = "pending"
    INITIALIZING = "initializing"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"

class ModuleState(BaseModel):
    """State tracking for a single module"""
    name: str
    status: ModuleStatus = ModuleStatus.PENDING
    current_step: int = 0
    total_steps: int = 0
    results: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    requires_input: bool = False
    input_description: Optional[str] = None

class ExecutionState(BaseModel):
    """Overall execution state tracking"""
    plan_id: str
    current_module_index: int = 0
    modules: Dict[str, ModuleState] = Field(default_factory=dict)
    global_state: Dict[str, Any] = Field(default_factory=dict)
    status: ModuleStatus = ModuleStatus.PENDING

class UserInputRequest(BaseModel):
    """Structure for requesting user input"""
    module_name: str
    description: str
    input_type: str = "text"  # could be 'text', 'number', 'boolean', etc.
    options: Optional[List[str]] = None

class Executer:
    """
    Manages the execution of study plan modules.
    Coordinates module initialization, execution flow, and state management.
    """
    
    def __init__(
        self,
        study_plan: StudyPlan,
        config_path: str = "config.json",
        log_path: str = "system.log"
    ):
        self.plan = study_plan
        self.config_path = Path(config_path)
        self.log_path = Path(log_path)
        self.state = ExecutionState(plan_id=study_plan.hypothesis_id)
        self._initialize_logging()
        
    def _initialize_logging(self):
        """Set up file logging"""
        file_handler = logging.FileHandler(self.log_path)
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )
        logger.addHandler(file_handler)
        
        

    async def initialize_modules(self):
        """Initialize all modules needed for execution"""
        logger.info(f"Initializing modules for plan {self.plan.hypothesis_id}")
        
        # Module mapping
        MODULE_CLASSES = {
            "PopulationSelector": "mas.modules.population.PopulationSelector",
            "ImagingProtocolDesigner": "mas.modules.imaging.ProtocolDesigner",
            "SegmentationModule": "mas.modules.segmentation.SegmentationModule",
            "RadiomicsExtractor": "mas.modules.radiomics.RadiomicsExtractor",
            "ModelingModule": "mas.modules.modeling.ModelingModule",
            "ValidationModule": "mas.modules.validation.ValidationModule"
        }
        
        for step in self.plan.steps:
            if not step.matched_module:
                logger.warning(f"Step '{step.name}' has no matched module")
                continue
                
            self.state.modules[step.matched_module] = ModuleState(
                name=step.matched_module,
                status=ModuleStatus.INITIALIZING,
                total_steps=1
            )
            
            try:
                # Dynamic module import and initialization
                if step.matched_module in MODULE_CLASSES:
                    module_path = MODULE_CLASSES[step.matched_module]
                    module_parts = module_path.split('.')
                    
                    # Import the module class
                    mod = __import__('.'.join(module_parts[:-1]), fromlist=[module_parts[-1]])
                    ModuleClass = getattr(mod, module_parts[-1])
                    
                    # Initialize the module
                    module_instance = ModuleClass(
                        config_path=str(self.config_path),
                        workspace_path="memory/workspace"
                    )
                    
                    # Store the instance
                    self.state.modules[step.matched_module].instance = module_instance
                    
                    # Initialize the module
                    if await module_instance.initialize():
                        self.state.modules[step.matched_module].status = ModuleStatus.PENDING
                        logger.info(f"Initialized module: {step.matched_module}")
                    else:
                        raise RuntimeError(f"Module {step.matched_module} failed to initialize")
                        
                else:
                    raise ValueError(f"Unknown module type: {step.matched_module}")
                    
            except Exception as e:
                logger.error(f"Failed to initialize module {step.matched_module}: {e}")
                self.state.modules[step.matched_module].status = ModuleStatus.FAILED
                self.state.modules[step.matched_module].error_message = str(e)

    def save_state(self):
        """Save current execution state to config file"""
        try:
            config = {}
            if self.config_path.exists():
                with open(self.config_path) as f:
                    config = json.load(f)
            
            config['execution_state'] = self.state.model_dump()
            
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
                
            logger.info("Saved execution state to config file")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def load_state(self):
        """Load execution state from config file"""
        try:
            if self.config_path.exists():
                with open(self.config_path) as f:
                    config = json.load(f)
                    if 'execution_state' in config:
                        self.state = ExecutionState(**config['execution_state'])
                        logger.info("Loaded execution state from config file")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    async def process_user_input(self, module_name: str, user_input: Any):
        """Handle user input for a module"""
        if module_name not in self.state.modules:
            raise ValueError(f"Unknown module: {module_name}")
            
        module = self.state.modules[module_name]
        if module.status != ModuleStatus.WAITING_INPUT:
            raise ValueError(f"Module {module_name} is not waiting for input")
            
        logger.info(f"Processing user input for module: {module_name}")
        module.requires_input = False
        module.input_description = None
        module.status = ModuleStatus.RUNNING
        
        # Here we would pass the input to the actual module
        # For now, we just update the state
        self.save_state()

    async def run(self):
        """
        Execute the study plan modules in sequence.
        Handles state transitions and user input requirements.
        """
        logger.info(f"Starting execution of plan {self.plan.hypothesis_id}")
        self.state.status = ModuleStatus.RUNNING
        
        await self.initialize_modules()
        self.save_state()
        
        try:
            current_step = 0
            while current_step < len(self.plan.steps):
                step = self.plan.steps[current_step]
                if not step.matched_module:
                    logger.warning(f"Skipping step {step.name} - no matched module")
                    current_step += 1
                    continue
                
                module = self.state.modules[step.matched_module]
                
                if module.status == ModuleStatus.FAILED:
                    logger.error(f"Module {step.matched_module} failed, stopping execution")
                    self.state.status = ModuleStatus.FAILED
                    break
                    
                if module.status == ModuleStatus.WAITING_INPUT:
                    # We need user input, pause execution
                    logger.info(f"Waiting for user input: {module.input_description}")
                    break
                
                if module.status in [ModuleStatus.PENDING, ModuleStatus.RUNNING]:
                    logger.info(f"Running module: {step.matched_module}")
                    # Here we would run the actual module
                    # For now, we just simulate completion
                    module.status = ModuleStatus.COMPLETED
                    self.save_state()
                
                if module.status == ModuleStatus.COMPLETED:
                    current_step += 1
                    self.state.current_module_index = current_step
                    self.save_state()
            
            if current_step == len(self.plan.steps):
                logger.info("All modules completed successfully")
                self.state.status = ModuleStatus.COMPLETED
            
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            self.state.status = ModuleStatus.FAILED
            raise
        
        finally:
            self.save_state()
        
        return self.state

async def main():
    """Example usage"""
    from .planner import Planner
    
    # Create a plan first
    planner = Planner()
    hypothesis = "A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma."
    plan_result = await planner.run(hypothesis, "")
    
    # Initialize and run executor
    executer = Executer(plan_result['plan'])
    state = await executer.run()
    
    print("\n🔄 Execution completed with status:", state.status)
    print("\nModule states:")
    for module_name, module in state.modules.items():
        print(f"- {module_name}: {module.status}")

if __name__ == "__main__":
    asyncio.run(main())