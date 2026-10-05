"""Error response port interface."""

from abc import ABC, abstractmethod
from typing import Any


class ErrorResponsePort(ABC):
    """Port interface for error response handling.

    This port defines the contract for error response structures.
    Infrastructure adapters provide concrete implementations.
    """

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:  # type: ignore[return]
        """Convert error response to dictionary.

        Returns:
            Dictionary representation of error response
        """
        pass

    @property
    @abstractmethod
    def error_code(self) -> str:  # type: ignore[return]
        """Get error code."""
        pass

    @property
    @abstractmethod
    def error_message(self) -> str:  # type: ignore[return]
        """Get error message."""
        pass

    @property
    @abstractmethod
    def status_code(self) -> int:  # type: ignore[return]
        """Get HTTP status code."""
        pass
