"""
UserInputHandler manages input requests from various system components.
Coordinates between the planner, executor, and modules for user interaction.
"""

import json
import logging
import asyncio
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class InputRequest(BaseModel):
    """Structure for user input requests"""
    id: str = Field(default_factory=lambda: f"req_{int(datetime.now().timestamp())}")
    source: str
    description: str
    input_type: str = "text"
    options: Optional[List[str]] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    response: Optional[Any] = None
    status: str = "pending"  # pending, completed, cancelled

class InputSource(str, Enum):
    """Source of input request"""
    PLANNER = "planner"
    EXECUTOR = "executor"
    MODULE = "module"

class UserInputHandler:
    """
    Manages user input requests from various system components.
    Provides both synchronous and asynchronous input methods.
    """
    
    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self.current_request: Optional[InputRequest] = None
        self.input_source: Optional[InputSource] = None
        self.history: List[InputRequest] = []
        self._load_saved_inputs()
    
    def _load_saved_inputs(self):
        """Load saved inputs and history from config"""
        try:
            if self.config_path.exists():
                with open(self.config_path) as f:
                    config = json.load(f)
                    # Load saved inputs
                    self.saved_inputs = config.get('user_inputs', {})
                    # Load input history
                    history_data = config.get('input_history', [])
                    self.history = [InputRequest(**item) for item in history_data]
            else:
                self.saved_inputs = {}
                self.history = []
        except Exception as e:
            logger.error(f"Failed to load saved inputs: {e}")
            self.saved_inputs = {}
            self.history = []
    
    def _save_input(self, key: str, value: Any):
        """Save user input to config file"""
        try:
            config = {}
            if self.config_path.exists():
                with open(self.config_path) as f:
                    config = json.load(f)
            
            if 'user_inputs' not in config:
                config['user_inputs'] = {}
            
            config['user_inputs'][key] = value
            
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save input: {e}")
    
    async def request_input(
        self,
        description: str,
        source: InputSource,
        input_type: str = "text",
        options: Optional[list] = None
    ) -> Any:
        """
        Request input from user asynchronously.
        Returns the user's input or raises TimeoutError.
        """
        # Create new request
        self.current_request = InputRequest(
            source=source.value,
            description=description,
            input_type=input_type,
            options=options
        )
        self.input_source = source
        
        # Add to history
        self.history.append(self.current_request)
        
        try:
            # In a real implementation, this would integrate with your UI
            # For now, we'll use console input
            print(f"\n[User Input Required - {source.value}]")
            print(f"Description: {description}")
            if options:
                print("Options:", ", ".join(options))
                
            response = input("Your input: ").strip()
            
            # Update request with response
            self.current_request.response = response
            self.current_request.status = "completed"
            
            # Save to config
            self._save_input(f"{source.value}_{description}", response)
            self._save_history()
            
            return response
            
        except Exception as e:
            self.current_request.status = "failed"
            logger.error(f"Failed to get user input: {e}")
            raise
            
        finally:
            self.current_request = None
            self.input_source = None
    
    def _save_history(self):
        """Save input history to config"""
        try:
            config = {}
            if self.config_path.exists():
                with open(self.config_path) as f:
                    config = json.load(f)
            
            config['input_history'] = [
                request.model_dump() for request in self.history
            ]
            
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save input history: {e}")
            
    def get_input_history(
        self,
        source: Optional[InputSource] = None,
        status: Optional[str] = None
    ) -> List[InputRequest]:
        """Get filtered input history"""
        filtered = self.history
        
        if source:
            filtered = [r for r in filtered if r.source == source.value]
        if status:
            filtered = [r for r in filtered if r.status == status]
            
        return filtered
