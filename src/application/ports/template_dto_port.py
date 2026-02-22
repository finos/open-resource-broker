"""Template DTO port interface."""

from abc import ABC, abstractmethod
from typing import Any


class TemplateDTOPort(ABC):
    """Port interface for template data transfer objects.
    
    This port defines the contract for template data structures used in the application layer.
    Infrastructure adapters provide concrete implementations.
    """

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Convert template to dictionary.
        
        Returns:
            Dictionary representation of template
        """
        ...

    @abstractmethod
    def from_dict(self, data: dict[str, Any]) -> "TemplateDTOPort":
        """Create template from dictionary.
        
        Args:
            data: Dictionary containing template data
            
        Returns:
            Template DTO instance
        """
        ...

    @property
    @abstractmethod
    def template_id(self) -> str:
        """Get template ID."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Get template name."""
        ...
