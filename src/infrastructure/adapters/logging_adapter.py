"""Logging adapter implementing LoggingPort."""

from typing import Any

from domain.base.ports.logging_port import LoggingPort
from infrastructure.logging.logger import get_logger


class LoggingAdapter(LoggingPort):
    """Adapter that implements LoggingPort using infrastructure logger."""

    def __init__(self, name: str = "application") -> None:
        """Initialize with logger name."""
        self._logger = get_logger(name)

    def _prepare_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Prepare kwargs with default stacklevel."""
        kwargs.setdefault("stacklevel", 2)
        return kwargs

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log debug message."""
        self._logger.debug(message, *args, **self._prepare_kwargs(kwargs))

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log info message."""
        self._logger.info(message, *args, **self._prepare_kwargs(kwargs))

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log warning message."""
        self._logger.warning(message, *args, **self._prepare_kwargs(kwargs))

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log error message."""
        self._logger.error(message, *args, **self._prepare_kwargs(kwargs))

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log critical message."""
        self._logger.critical(message, *args, **self._prepare_kwargs(kwargs))

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log exception with traceback."""
        self._logger.exception(message, *args, **self._prepare_kwargs(kwargs))

    def log(self, level: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log message at specified level."""
        self._logger.log(level, message, *args, **self._prepare_kwargs(kwargs))
