"""
Base class for all executable modules in the system.
"""

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class ModuleConfig(BaseModel):
    """Base configuration for all modules"""
    name: str
    version: str = "1.0"
    description: str = ""
    parameters: Dict[str, Any] = {}

class ModuleInterface(ABC):
    """Abstract base class for all modules"""
    
    def __init__(
        self,
        config_path: str = "config.json",
        workspace_path: str = "memory/workspace"
    ):
        self.config_path = Path(config_path)
        self.workspace = Path(workspace_path)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.status = "initialized"
        self._load_config()
    
    def _load_config(self):
        """Load module configuration from config file"""
        try:
            if self.config_path.exists():
                with open(self.config_path) as f:
                    config = json.load(f)
                    module_config = config.get(self.get_name(), {})
                    self.config = ModuleConfig(
                        name=self.get_name(),
                        **module_config
                    )
            else:
                self.config = ModuleConfig(name=self.get_name())
        except Exception as e:
            logger.error(f"Failed to load config for module {self.get_name()}: {e}")
            self.config = ModuleConfig(name=self.get_name())
    
    def save_state(self):
        """Save module state to config file"""
        try:
            config = {}
            if self.config_path.exists():
                with open(self.config_path) as f:
                    config = json.load(f)
            
            config[self.get_name()] = {
                "status": self.status,
                "config": self.config.model_dump()
            }
            
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state for module {self.get_name()}: {e}")
    
    @abstractmethod
    def get_name(self) -> str:
        """Return the unique name of this module"""
        pass
    
    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the module with required resources"""
        pass
    
    @abstractmethod
    async def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run the module's main functionality"""
        pass
    
    @abstractmethod
    async def cleanup(self):
        """Clean up any resources used by the module"""
        pass
    
    def requires_input(self) -> Optional[str]:
        """Return description of required input or None"""
        return None