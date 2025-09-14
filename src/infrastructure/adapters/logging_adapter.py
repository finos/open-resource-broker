"""Logging adapter implementing LoggingPort."""

from typing import Any

from domain.base.ports.logging_port import LoggingPort
from infrastructure.logging.logger import get_logger


class LoggingAdapter(LoggingPort):
    """Adapter that implements LoggingPort using infrastructure logger."""

    def __init__(self, name: str = "application") -> None:
        """Initialize with logger name."""
        self._logger = get_logger(name)

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log debug message."""
        kwargs.setdefault('stacklevel', 2)
        self._logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log info message."""
        kwargs.setdefault('stacklevel', 2)
        self._logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log warning message."""
        kwargs.setdefault('stacklevel', 2)
        self._logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log error message."""
        kwargs.setdefault('stacklevel', 2)
        self._logger.error(message, *args, **kwargs)

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log critical message."""
        kwargs.setdefault('stacklevel', 2)
        self._logger.critical(message, *args, **kwargs)

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log exception with traceback."""
        kwargs.setdefault('stacklevel', 2)
        self._logger.exception(message, *args, **kwargs)

    def log(self, level: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log message at specified level."""
        kwargs.setdefault('stacklevel', 2)
        self._logger.log(level, message, *args, **kwargs)
