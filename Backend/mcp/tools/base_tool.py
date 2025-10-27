from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Any, Dict

class BaseTool(ABC):
    """Base class for all MCP tools."""
    
    name: str
    description: str
    
    def __init__(self):
        self.initialized = False
        
    @abstractmethod
    def initialize(self) -> None:
        """Initialize any resources needed by the tool."""
        pass
        
    @abstractmethod
    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run the tool's main functionality."""
        pass
        
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """Validate input data."""
        return True
        
    def cleanup(self) -> None:
        """Cleanup any resources."""
        pass