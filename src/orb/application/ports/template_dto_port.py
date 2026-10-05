"""Template DTO port interface."""

from abc import ABC, abstractmethod
from typing import Any


class TemplateDTOPort(ABC):
    """Port interface for template data transfer objects.

    This port defines the contract for template data structures used in the application layer.
    Infrastructure adapters provide concrete implementations.
    """

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:  # type: ignore[return]
        """Convert template to dictionary.

        Returns:
            Dictionary representation of template
        """
        pass

    @abstractmethod
    def from_dict(self, data: dict[str, Any]) -> "TemplateDTOPort":  # type: ignore[return]
        """Create template from dictionary.

        Args:
            data: Dictionary containing template data

        Returns:
            Template DTO instance
        """
        pass

    @property
    @abstractmethod
    def template_id(self) -> str:  # type: ignore[return]
        """Get template ID."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:  # type: ignore[return]
        """Get template name."""
        pass
