"""
System Event Handlers - CQRS-aligned handlers using BaseEventHandler pattern.

These handlers follow the same architectural patterns as BaseCommandHandler and
BaseQueryHandler, ensuring consistency across all handler types in the CQRS system.
"""

from typing import Optional

from application.base.event_handlers import BaseLoggingEventHandler
from application.events.decorators import event_handler
from domain.base.events import DomainEvent
from domain.base.ports import ErrorHandlingPort, EventPublisherPort, LoggingPort


@event_handler("SystemStartedEvent")
class SystemStartedHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle system startup events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ):
        """Initialize system started handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format system started log message."""
        version = getattr(event, "version", "unknown")
        startup_time = getattr(event, "startup_time", "unknown")

        return (
            f"System started successfully | "
            f"Version: {version} | "
            f"Startup time: {startup_time}s"
        )

    def get_log_level(self, event: DomainEvent) -> str:
        """Get log level - info for system startup."""
        return "info"


@event_handler("SystemShutdownEvent")
class SystemShutdownHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle system shutdown events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ):
        """Initialize system shutdown handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format system shutdown log message."""
        reason = getattr(event, "shutdown_reason", "unknown")
        graceful = getattr(event, "graceful_shutdown", True)

        shutdown_type = "graceful" if graceful else "forced"

        return f"System shutdown initiated | " f"Type: {shutdown_type} | " f"Reason: {reason}"

    def get_log_level(self, event: DomainEvent) -> str:
        """Get log level - warning for forced shutdowns."""
        graceful = getattr(event, "graceful_shutdown", True)
        return "info" if graceful else "warning"


@event_handler("ConfigurationUpdatedEvent")
class ConfigurationUpdatedHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle configuration update events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ):
        """Initialize configuration updated handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format configuration updated log message."""
        config_section = getattr(event, "config_section", "unknown")
        changed_keys = getattr(event, "changed_keys", [])

        keys_str = ", ".join(changed_keys) if changed_keys else "unknown"

        return (
            f"Configuration updated | " f"Section: {config_section} | " f"Changed keys: {keys_str}"
        )

    def get_log_level(self, event: DomainEvent) -> str:
        """Get log level - info for configuration updates."""
        return "info"
