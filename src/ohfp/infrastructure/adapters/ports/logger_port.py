"""Logger port - Domain interface for logging."""

from abc import ABC, abstractmethod
from typing import Any


class LoggerPort(ABC):
    """Port for logging functionality - allows domain to log without depending on infrastructure."""

    @abstractmethod
    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log debug message."""

    @abstractmethod
    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log info message."""

    @abstractmethod
    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log warning message."""

    @abstractmethod
    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log error message."""

    @abstractmethod
    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log critical message."""


class EventPublisherPort(ABC):
    """Port for event publishing - allows domain to publish events without depending on infrastructure."""

    @abstractmethod
    def publish(self, event: Any) -> None:
        """Publish a domain event."""

    @abstractmethod
    def publish_batch(self, events: list[Any]) -> None:
        """Publish multiple domain events."""
