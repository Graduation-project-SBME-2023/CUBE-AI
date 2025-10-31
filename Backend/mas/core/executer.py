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

from agent_registry import get_agent

import inspect
import importlib
import pkgutil
import logging
from mas import agents  # assuming modules are in mas/agents

logger = logging.getLogger(__name__)


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
    # Add a place to keep the live module instance (not serialized ideally, but kept here for convenience)
    instance: Optional[Any] = None

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
        study_plan: dict,
        config_path: str = "config.json",
        log_path: str = "system.log"
    ):
        self.plan = study_plan
        self.config_path = Path(config_path)
        self.log_path = Path(log_path)
        self.state = ExecutionState(plan_id=study_plan['hypothesis_id'])
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
        logger.info(f"Initializing modules for plan {self.plan['hypothesis_id']}")

        for step in self.plan['steps']:
            if not step.get('matched_module'):
                logger.warning(f"Step '{step['name']}' has no matched module")
                continue

            module_name = step['matched_module']
            # create ModuleState and store it
            self.state.modules[module_name] = ModuleState(
                name=module_name,
                status=ModuleStatus.INITIALIZING,
                total_steps=1
            )

            try:
                agent_info = get_agent(module_name)
                if not agent_info:
                    raise ValueError(f"Unknown module type: {module_name}")

                module_instance = agent_info.cls(
                    config_path=str(self.config_path)
                )

                # store instance on the ModuleState *using attribute access*
                self.state.modules[module_name].instance = module_instance

                # initialize instance
                if await module_instance.initialize():
                    self.state.modules[module_name].status = ModuleStatus.PENDING
                    logger.info(f"Initialized module: {module_name}")
                else:
                    raise RuntimeError(f"Module {module_name} failed to initialize")

            except Exception as e:
                logger.error(f"Failed to initialize module {module_name}: {e}")
                self.state.modules[module_name].status = ModuleStatus.FAILED
                self.state.modules[module_name].error_message = str(e)


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
        Execute all discovered modules in sequence (ordered, unique).
        """
        logger.info(f"Starting execution of plan {self.plan['hypothesis_id']}")
        self.state.status = ModuleStatus.RUNNING

        # Initialize all modules referenced by the plan
        await self.initialize_modules()
        self.save_state()

        try:
            # Build ordered, deduplicated module list from the plan's steps (prefer planner order)
            ordered_modules = []
            for step in self.plan['steps']:
                m = step.get('matched_module')
                if m:
                    ordered_modules.append(m)

            # Remove duplicates while preserving order
            seen = set()
            unique_ordered_modules = []
            for m in ordered_modules:
                if m not in seen:
                    unique_ordered_modules.append(m)
                    seen.add(m)

            logger.info(f"Modules to run (ordered, unique): {unique_ordered_modules}")

            # Run each module sequentially
            for idx, module_name in enumerate(unique_ordered_modules):
                logger.info(f"Running module [{idx}]: {module_name}")
                # update index
                self.state.current_module_index = idx
                self.save_state()

                # Ensure ModuleState exists
                module_state = self.state.modules.get(module_name)
                if module_state is None:
                    # create a ModuleState if initialize_modules skipped it
                    module_state = ModuleState(name=module_name, status=ModuleStatus.PENDING, total_steps=1)
                    self.state.modules[module_name] = module_state

                if module_state.status == ModuleStatus.FAILED:
                    logger.error(f"Module {module_name} previously failed, stopping execution")
                    self.state.status = ModuleStatus.FAILED
                    break

                if module_state.status == ModuleStatus.WAITING_INPUT:
                    logger.info(f"Waiting for user input for {module_name}: {module_state.input_description}")
                    break

                # Run if pending or running
                if module_state.status in (ModuleStatus.PENDING, ModuleStatus.RUNNING, ModuleStatus.INITIALIZING):
                    module_state.status = ModuleStatus.RUNNING
                    self.save_state()

                    try:
                        module_instance = module_state.instance
                        if module_instance is None:
                            # Attempt to obtain via registry if missing
                            agent_info = get_agent(module_name)
                            if not agent_info:
                                raise RuntimeError(f"No agent info for {module_name}")
                            module_instance = agent_info.cls(config_path=str(self.config_path))
                            # optional: initialize newly created instance
                            if hasattr(module_instance, "initialize") and inspect.iscoroutinefunction(module_instance.initialize):
                                await module_instance.initialize()
                            module_state.instance = module_instance

                        # call run; modules expect the plan/hypothesis
                        # if module.run is async, await it; otherwise call it normally
                        run_coro = getattr(module_instance, "run", None)
                        if run_coro is None:
                            raise RuntimeError(f"Module {module_name} has no run()")

                        # support both async and sync run()
                        if inspect.iscoroutinefunction(run_coro):
                            result = await run_coro(self.plan)
                        else:
                            result = run_coro(self.plan)

                        # detect a request for interactive input
                        if isinstance(result, dict) and result.get("requires_input"):
                            module_state.status = ModuleStatus.WAITING_INPUT
                            module_state.requires_input = True
                            module_state.input_description = result.get("description", "User input required.")
                            module_state.results = result
                            logger.info(f"Module {module_name} is waiting for user input.")
                            self.save_state()
                            # pause execution so external caller can supply input
                            break

                        # Save successful results to ModuleState (keep ModuleState typed)
                        module_state.results = result if isinstance(result, dict) else {"output": result}
                        module_state.status = ModuleStatus.COMPLETED
                        logger.info(f"Module {module_name} completed successfully.")

                    except Exception as exc:
                        module_state.status = ModuleStatus.FAILED
                        module_state.error_message = str(exc)
                        logger.error(f"Error while running module {module_name}: {exc}")

                    # persist after each module
                    self.save_state()

            # If we finished the loop without breaks due to waiting/input or failures:
            all_completed = all(
                (m.status == ModuleStatus.COMPLETED) for m in self.state.modules.values()
            )
            if all_completed:
                logger.info("✅ All modules processed and completed.")
                self.state.status = ModuleStatus.COMPLETED

        except Exception as e:
            logger.error(f"Execution failed: {e}")
            self.state.status = ModuleStatus.FAILED
            raise

        finally:
            self.save_state()

        return self.state

def _load_json_file(file_path: str) -> Dict[str, Any]:
    """
    Load a JSON file and return its contents as a Python dict.

    Parameters
    ----------
    file_path: str
        Path to the JSON file.  Can be a raw string or a normal string
        with escaped back‑slashes.

    Returns
    -------
    dict
        Parsed JSON content.
    """
    # Use a raw string when calling the helper, or double‑escape the path.
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("JSON file not found: %s", file_path)
        raise
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in %s: %s", file_path, exc)
        raise
    
    
    
async def main():
    """Example usage"""
    from planner import Planner
    
    # Create a plan first
    # planner = Planner()
    # hypothesis = "A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma."
    # plan_result = await planner.run(hypothesis, "")
    
    json_path = r"D:\SBME\GP\OHIF-Standalone\Backend\mas\memory\history\hypo.json"
    plan_result = _load_json_file(json_path)
    plan_result = plan_result["plan"]

    # Initialize and run executor
    executer = Executer(plan_result)
    state = await executer.run()
    
    print("\n🔄 Execution completed with status:", state.status)
    print("\nModule states:")
    for module_name, module in state.modules.items():
        print(f"- {module_name}: {module.status}")

if __name__ == "__main__":
    asyncio.run(main())