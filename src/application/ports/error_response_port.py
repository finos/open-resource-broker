"""Error response port interface."""

from abc import ABC, abstractmethod
from typing import Any


class ErrorResponsePort(ABC):
    """Port interface for error response handling.
    
    This port defines the contract for error response structures.
    Infrastructure adapters provide concrete implementations.
    """

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Convert error response to dictionary.
        
        Returns:
            Dictionary representation of error response
        """
        ...

    @property
    @abstractmethod
    def error_code(self) -> str:
        """Get error code."""
        ...

    @property
    @abstractmethod
    def error_message(self) -> str:
        """Get error message."""
        ...

    @property
    @abstractmethod
    def status_code(self) -> int:
        """Get HTTP status code."""
        ...
