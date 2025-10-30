'''
Every agent has:
    - Status
    - Dependency (input description)
    - Pipeline: Detailed description of the steps
    - Evaluator: To evaluate agent output making with a description of good output
'''
class BaseAgent:
    def __init__(self, name, config=None):
        self.name = name
        self.config = config or {}
        self.state = "idle"
        self.pending_key = None  # if waiting for user input

    def run(self, input_data):
        """
        Run the agent task.
        Return dict:
          - {"status": "finished", "output": {...}}  OR
          - {"status": "require_input", "description": "...", "key": "..."}
        """
        
        # functions of steps (loop - raise confirmation flag )
    
        # Must Read the Status
        # If there multistep start based on the status
        
        raise NotImplementedError

    def save_state(self, state_file):
        import json
        with open(state_file, "w") as f:
            json.dump({
                "name": self.name,
                "state": self.state,
                "pending_key": self.pending_key
            }, f, indent=2)
