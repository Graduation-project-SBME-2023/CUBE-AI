class BaseAgent:
    def __init__(self, name, config=None):
        self.name = name
        self.config = config or {}
        self.state = "idle"
        self.pending_key = None  # if waiting for user input

    def run(self, input_data):
        """Run the agent task.
        Return dict:
          - {"status": "finished", "output": {...}}  OR
          - {"status": "require_input", "description": "...", "key": "..."}
        """
        raise NotImplementedError

    def save_state(self, state_file):
        import json
        with open(state_file, "w") as f:
            json.dump({
                "name": self.name,
                "state": self.state,
                "pending_key": self.pending_key
            }, f, indent=2)
