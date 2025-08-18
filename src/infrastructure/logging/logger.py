"""Application logging configuration."""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config import LoggingConfig


class JsonFormatter(logging.Formatter):
    """Format log records as JSON."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the instance."""
        super().__init__()
        self.default_fields = kwargs

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        # Get file path relative to project root if possible
        file_path = record.pathname
        try:
            # Try to get relative path from src directory
            src_index = file_path.find("/src/")
            if src_index >= 0:
                file_path = file_path[src_index + 1 :]  # +1 to remove leading slash
        except Exception as e:
            # Can't use logger here to avoid recursion
            # Just use full path and continue
            print(  # noqa: logging bootstrap
                f"Warning: Error formatting log path: {e}"
            )  # Simple console output for logging system errors

        message = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "file": file_path,
            "location": f"{file_path}:{record.lineno} ({record.funcName})",
            **self.default_fields,
        }

        if record.exc_info:
            message["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "request_id"):
            message["request_id"] = record.request_id

        if hasattr(record, "correlation_id"):
            message["correlation_id"] = record.correlation_id

        # Include any extra fields provided in the log call
        if hasattr(record, "extra"):
            message.update(record.extra)

        return json.dumps(message)


class ContextLogger(logging.Logger):
    """Logger that supports context information."""

    def __init__(self, name: str, level: int = logging.NOTSET) -> None:
        """Initialize context logger with name and level."""
        super().__init__(name, level)
        self._context: Dict[str, Any] = {}

    def bind(self, **kwargs: Any) -> None:
        """Bind context values to logger."""
        self._context.update(kwargs)

    def unbind(self, *keys: str) -> None:
        """Remove context values from logger."""
        for key in keys:
            self._context.pop(key, None)

    def _log(
        self,
        level: int,
        msg: str,
        args: tuple,
        exc_info: Optional[Exception] = None,
        extra: Optional[Dict[str, Any]] = None,
        stack_info: bool = False,
        stacklevel: int = 1,
    ) -> None:
        """Override _log to include context information."""
        if extra is None:
            extra = {}
        extra.update(self._context)
        # Increase stacklevel by 1 to skip our wrapper and report the correct caller
        super()._log(level, msg, args, exc_info, extra, stack_info, stacklevel + 1)


# Flag to track if logging has been initialized
_logging_initialized = False


def setup_logging(config: LoggingConfig) -> None:
    """
    Configure application logging.

    Args:
        config: Logging configuration
    """
    global _logging_initialized

    # Only initialize logging once
    if _logging_initialized:
        return

    # Set logging class
    logging.setLoggerClass(ContextLogger)

    # Create root logger
    root_logger = logging.getLogger()

    # Convert string level to numeric level
    level_name = config.level.upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger.setLevel(level)

    # Log the level being set
    logger = get_logger(__name__)
    logger.info("Setting root logger level to: %s", level_name)

    # Remove any existing handlers to prevent duplicates
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    # Create formatters
    json_formatter = JsonFormatter()
    text_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s [%(pathname)s:%(lineno)d (%(funcName)s)]"
    )

    # Configure console logging based on config
    # Use ConfigurationManager to get console_enabled value
    from config.manager import get_config_manager

    try:
        config_manager = get_config_manager()
        console_enabled = config_manager.get(
            "logging.console_enabled", config.console_enabled)
        if isinstance(console_enabled, str):
            console_enabled = console_enabled.lower() in ("true", "1", "yes")
    except Exception as e:
        # Fallback to config if ConfigurationManager fails
        logger.debug(
            "Could not get console_enabled from ConfigurationManager: %s", str(e))
        console_enabled = config.console_enabled

    if console_enabled:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(text_formatter)
        root_logger.addHandler(console_handler)

    # Configure file logging if path provided
    if config.file_path:
        # Create log directory if needed
        log_path = Path(config.file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Create rotating file handler
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(log_path),
            maxBytes=config.max_size,
            backupCount=config.backup_count,
        )
        file_handler.setFormatter(json_formatter)
        root_logger.addHandler(file_handler)

    # Set default logging levels for third-party libraries
    get_logger("boto3").setLevel(logging.WARNING)
    get_logger("botocore").setLevel(logging.WARNING)
    get_logger("urllib3").setLevel(logging.WARNING)

    # Mark logging as initialized
    _logging_initialized = True

    # Log initialization
    logging.getLogger(__name__).debug("Logging system initialized")


def get_logger(name: str) -> ContextLogger:
    """
    Get a logger instance.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


class LoggerAdapter(logging.LoggerAdapter):
    """Adapter that adds context to log records."""

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """Process log record to add context."""
        if "extra" not in kwargs:
            kwargs["extra"] = {}
        kwargs["extra"].update(self.extra)
        return msg, kwargs


def with_context(**context: Any) -> LoggerAdapter:
    """
    Create a logger adapter with context.

    Args:
        **context: Context key-value pairs

    Returns:
        Logger adapter with context
    """
    logger = get_logger(__name__)
    return LoggerAdapter(logger, context)


class RequestLogger:
    """Logger for request-specific logging."""

    def __init__(self, request_id: str, correlation_id: Optional[str] = None) -> None:
        self.logger = with_context(
            request_id=request_id, correlation_id=correlation_id or request_id
        )

    def info(self, msg: str, **kwargs: Any) -> None:
        """Log info message."""
        self.logger.info(msg, extra=kwargs)

    def error(self, msg: str, exc_info: Optional[Exception] = None, **kwargs: Any) -> None:
        """Log error message."""
        self.logger.error(msg, exc_info=exc_info, extra=kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        """Log warning message."""
        self.logger.warning(msg, extra=kwargs)

    def debug(self, msg: str, **kwargs: Any) -> None:
        """Log debug message."""
        self.logger.debug(msg, extra=kwargs)


class AuditLogger:
    """Logger for audit events."""

    def __init__(self) -> None:
        self.logger = get_logger("audit")

    def log_event(
        self,
        event_type: str,
        user: str,
        action: str,
        resource: str,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log an audit event.

        Args:
            event_type: Type of event
            user: User performing the action
            action: Action being performed
            resource: Resource being acted upon
            status: Status of the action
            details: Additional event details
        """
        self.logger.info(
            "%s: %s on %s by %s - %s", event_type, action, resource, user, status,
            extra={
                "event_type": event_type,
                "user": user,
                "action": action,
                "resource": resource,
                "status": status,
                "details": details or {},
                "timestamp": datetime.utcnow().isoformat(),
            },
        )


class MetricsLogger:
    """Logger for application metrics."""

    def __init__(self) -> None:
        self.logger = get_logger("metrics")

    def log_timing(
        self, operation: str, duration_ms: float, status: str = "success", **tags: str
    ) -> None:
        """
        Log operation timing.

        Args:
            operation: Operation being timed
            duration_ms: Duration in milliseconds
            status: Operation status
            **tags: Additional metric tags
        """
        self.logger.info(
            "%s took %sms", operation, duration_ms:.2f,
            extra={
                "metric_type": "timing",
                "operation": operation,
                "duration_ms": duration_ms,
                "status": status,
                "tags": tags,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    def log_counter(self, metric: str, value: int = 1, **tags: str) -> None:
        """
        Log counter metric.

        Args:
            metric: Metric name
            value: Metric value
            **tags: Additional metric tags
        """
        self.logger.info(
            "%s: %s", metric, value,
            extra={
                "metric_type": "counter",
                "metric": metric,
                "value": value,
                "tags": tags,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    def log_gauge(self, metric: str, value: float, **tags: str) -> None:
        """
        Log gauge metric.

        Args:
            metric: Metric name
            value: Metric value
            **tags: Additional metric tags
        """
        self.logger.info(
            "%s: %s", metric, value,
            extra={
                "metric_type": "gauge",
                "metric": metric,
                "value": value,
                "tags": tags,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
