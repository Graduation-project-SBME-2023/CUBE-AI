"""
Simple orchestrator  receives a hypothesis, runs the Planner,
then runs the Executer on the generated study plan.
"""

import asyncio
import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from planner import Planner, ClinicalHypothesis  # absolute import
from executer import Executer, ModuleStatus
from user_input_handler import FeedbackAgent, OutputFormattingAgent
# ----------------------------------------------------------------------
# Logging (one logger for the whole orchestrator)
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# System‑wide state enum – we only need a few states for this simple flow
# ----------------------------------------------------------------------
class SystemState(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    CHATTING = "chatting"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


# ----------------------------------------------------------------------
# Orchestrator implementation
# ----------------------------------------------------------------------
class Orchestrator:
    """
    Very small orchestrator:

        Run the Planner on the supplied hypothesis.
        Initialise the Executer with the plan returned by the Planner.
        Run the Executer and return the combined result.
    """

    def __init__(
        self,
        config_path: str = "config.json",
        log_path: str = "system.log",
    ):
        self.config_path = Path(config_path)
        self.log_path = Path(log_path)
        self.state = SystemState.IDLE
        
        current_dir = Path(__file__).resolve().parent
        json_path = current_dir.parent / "memory" / "workspace" / "state.json"

        if not json_path.exists():
            logger.warning(f"State file not found at {json_path}")
            raise FileNotFoundError(f"Missing state.json at {json_path}")

        # Load stored hypothesis and plan
        with open(json_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        self.current_hypothesis = config.get("hypothesis", "")
        self.current_study_plan = config.get("plan", "")
        # The Planner does not need any arguments at construction time –
        # it will receive the hypothesis when ``run`` is called.
        self.planner = Planner(config_path=str(self.config_path), log_path=str(self.log_path))
        self.require_input = False
        self.user_input_handler = FeedbackAgent()
        self.output_formatter = OutputFormattingAgent()
    # ------------------------------------------------------------------
    # Helper to persist the orchestrator state (optional – useful for debugging)
    # ------------------------------------------------------------------
    def _save_state(self):
        try:
            cfg = {}
            if self.config_path.exists():
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)

            cfg["orchestrator_state"] = self.state.value
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception as exc:
            logger.error("Failed to persist orchestrator state: %s", exc)

    def run(self):
        
        if self.current_hypothesis == None or self.require_input == True :
                 result = self.user_input_handler.handle_user_input()
                 if result['finished'] == True:
                     return result
        else:
            result = self.process_hypothesis(hypothesis_text= self.current_hypothesis)
            if result['status'] == "require_input":
                self.require_input == True
                
            
        
       
   
    # ------------------------------------------------------------------
    # Core method – receives a hypothesis string (or ClinicalHypothesis) and
    # runs the whole pipeline.
    # ------------------------------------------------------------------
    
    async def process_hypothesis(
        self,
        hypothesis_text: str,
    ) -> Dict[str, Any]:
        """
        Run the full pipeline.

        Parameters
        ----------
        hypothesis_text : str
            Free-form description of the clinical hypothesis.
        tools_doc : str, optional
            Documentation of the available modules - passed straight to the
            Planner (the default empty string works because the Planner
            already knows the module list via the agent registry).

        Returns
        -------
        dict
            ``{
                "planner_result": {...},
                "execution_state": {...},
                "system_state": "completed" | "failed"
            }``
        """
        try:
            # --------------------------------------------------------------
            # 1️⃣  Planning phase
            # --------------------------------------------------------------
    
            self.state = SystemState.PLANNING
            self._save_state()
            logger.info("Starting planning phase")
            if self.current_hypothesis['title'] == "":
                planner_result = await self.planner.run(hypothesis_text)


                if planner_result.get("status") != "finished":
                    raise RuntimeError(
                        f"Planner finished with unexpected status: {planner_result.get('status')}"
                    )
                    
                if planner_result.get("status") == "finished" and self.state == SystemState.PLANNING:
                    self.state == SystemState.CHATTING
                    return await self.output_formatter.format_output(planner_result)

            # --------------------------------------------------------------
            #  FeedBack phase
            # --------------------------------------------------------------
            if self.current_hypothesis != None:
                feedback = await self.user_input_handler.chat()
                
                if feedback['status'] == 'pending':
                    print("feedback", feedback)
                    return await self.output_formatter.format_output(feedback)
                
                elif feedback.status == 'finished':
                    self.status = SystemState.EXECUTING

            # --------------------------------------------------------------
            #  Execution phase
            # --------------------------------------------------------------
            if self.state == SystemState.EXECUTING:
                self._save_state()
                logger.info("Starting execution phase")

                # ``planner_result['plan']`` is a plain dict – the Executer expects that.
                executer = Executer(
                    study_plan=planner_result["plan"],
                    config_path=str(self.config_path),
                    log_path=str(self.log_path),
                )
                execution_state = await executer.run()

            # --------------------------------------------------------------
            # 3️⃣  Final status
            # --------------------------------------------------------------
            if execution_state.status == ModuleStatus.COMPLETED:
                self.state = SystemState.COMPLETED
                

                current_dir = Path(__file__).resolve().parent
                report_path = current_dir.parent / "memory" / "workspace" / "final_report.json"
                report = json.load(report_path)
                return await self.output_formatter.format_output(report)
            else:
                self.state = SystemState.FAILED
                

            self._save_state()

            return {
                "planner_result": planner_result,
                "execution_state": execution_state.model_dump(),
                "system_state": self.state.value,
            }

        except Exception as exc:
            logger.error("Pipeline failed: %s", exc)
            self.state = SystemState.FAILED
            self._save_state()
            raise

# ----------------------------------------------------------------------
# Example entry‑point – run the orchestrator from the command line
# ----------------------------------------------------------------------
async def main():
    orchestrator = Orchestrator()

    hypothesis = (
        "A radiomics‑derived imaging signature from CT imaging can predict high‑risk "
        "patients with neuroblastoma."
    )

    # You can optionally provide a tools description – the Planner already has a
    # built‑in fallback that lists the agents, so an empty string works.


    result = await orchestrator.process_hypothesis(hypothesis)

    print("\n Pipeline finished – overall system state:")
    print("\nPlanner output (truncated):")
    print(json.dumps(result["planner_result"], indent=2)[:500] + " ...")
    print("\nExecution summary:")
    exec_state = result["execution_state"]
    print(f"  - Overall status: {exec_state['status']}")
    print("  - Module outcomes:")
    for name, mod in exec_state["modules"].items():
        print(f"    * {name}: {mod['status']}")

if __name__ == "__main__":
    asyncio.run(main())