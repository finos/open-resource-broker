"""Logging port for domain and application layers."""

from abc import ABC, abstractmethod
from typing import Any


class LoggingPort(ABC):
    """Port for logging operations - abstracts infrastructure logging concerns."""

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

    @abstractmethod
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log exception with traceback."""

    @abstractmethod
    def log(self, level: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log message at specified level."""
