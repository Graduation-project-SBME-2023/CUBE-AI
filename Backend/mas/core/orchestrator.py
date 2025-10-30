"""
Orchestrator coordinates the medical analysis system components:
- Planner: Creates study plans from hypotheses
- Executer: Runs the study plan modules
- UserInputHandler: Manages user interactions
"""

import asyncio
import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Union
from pydantic import BaseModel, Field

from .planner import Planner, ClinicalHypothesis
from .executer import Executer, ModuleStatus, UserInputRequest

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SystemState(str, Enum):
    """Overall system state"""
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"

class Orchestrator:
    """
    Main system orchestrator that coordinates:
    - Planner for study design
    - Executer for running modules
    - UserInputHandler for managing interactions
    """
    
    def __init__(
        self,
        config_path: str = "config.json",
        log_path: str = "system.log"
    ):
        self.config_path = Path(config_path)
        self.log_path = Path(log_path)
        self.state = SystemState.IDLE
        
        # Initialize components
        self.input_handler = UserInputHandler(config_path)
        self.planner = Planner()  # Will be initialized with hypothesis when needed
        self.executer = None  # Will be initialized with plan when needed
        
        # Set up logging
        self._initialize_logging()
        
    def _initialize_logging(self):
        """Set up system-wide logging"""
        file_handler = logging.FileHandler(self.log_path)
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )
        logger.addHandler(file_handler)
    
    def save_state(self):
        """Save orchestrator state to config"""
        try:
            config = {}
            if self.config_path.exists():
                with open(self.config_path) as f:
                    config = json.load(f)
            
            config['orchestrator_state'] = self.state
            
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save orchestrator state: {e}")
    
    async def process_hypothesis(self, hypothesis_text: str) -> Dict[str, Any]:
        """
        Process a new hypothesis through the complete pipeline:
        1. Planning phase
        2. Execution phase
        3. Result collection
        """
        try:
            # -------- Planning Phase --------
            self.state = SystemState.PLANNING
            self.save_state()
            
            logger.info(f"Starting planning phase for hypothesis: {hypothesis_text}")
            planning_result = await self.planner.run(
                hypothesis_text,
                tools_doc=""  # You might want to load this from config
            )
            
            if planning_result['status'] == 'finished':
                logger.info("Planning phase completed successfully")
            else:
                raise ValueError(f"Planning failed with status: {planning_result['status']}")
            
            # -------- Execution Phase --------
            self.state = SystemState.EXECUTING
            self.save_state()
            
            logger.info("Initializing execution phase")
            self.executer = Executer(
                study_plan=planning_result['plan'],
                config_path=str(self.config_path),
                log_path=str(self.log_path)
            )
            
            execution_state = await self.executer.run()
            
            if execution_state.status == ModuleStatus.COMPLETED:
                logger.info("Execution phase completed successfully")
                self.state = SystemState.COMPLETED
            elif execution_state.status == ModuleStatus.WAITING_INPUT:
                logger.info("Execution paused - waiting for user input")
                self.state = SystemState.WAITING_INPUT
            else:
                logger.error(f"Execution failed with status: {execution_state.status}")
                self.state = SystemState.FAILED
            
            self.save_state()
            
            return {
                "planning_result": planning_result,
                "execution_state": execution_state.model_dump(),
                "system_state": self.state
            }
            
        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            self.state = SystemState.FAILED
            self.save_state()
            raise

    async def handle_user_input(self, input_data: Any) -> Dict[str, Any]:
        """Process user input and continue pipeline execution"""
        if self.state != SystemState.WAITING_INPUT:
            raise ValueError("System is not waiting for input")
            
        if self.input_handler.input_source == InputSource.EXECUTOR:
            # Pass input to executor and continue
            await self.executer.process_user_input(
                self.input_handler.current_request.module_name,
                input_data
            )
            return await self.executer.run()
        
        elif self.input_handler.input_source == InputSource.PLANNER:
            # Update planner and rerun
            # (You might need to implement this in your Planner class)
            return await self.planner.run(input_data, "")
            
        else:
            raise ValueError(f"Unknown input source: {self.input_handler.input_source}")

async def main():
    """Example usage of the orchestrator"""
    orchestrator = Orchestrator()
    
    # Process a hypothesis
    hypothesis = "A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma."
    
    try:
        result = await orchestrator.process_hypothesis(hypothesis)
        print("\n🎯 Pipeline Result:")
        print(json.dumps(result, indent=2))
        
        # Example: Handle user input if needed
        if orchestrator.state == SystemState.WAITING_INPUT:
            user_input = await orchestrator.input_handler.request_input(
                "Please confirm the population selection criteria",
                InputSource.EXECUTOR
            )
            result = await orchestrator.handle_user_input(user_input)
            print("\n🔄 Pipeline Continued:")
            print(json.dumps(result, indent=2))
            
    except Exception as e:
        print(f"❌ Pipeline failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())