"""
Machine Event Handlers - CQRS-aligned handlers using BaseEventHandler pattern.

These handlers follow the same architectural patterns as BaseCommandHandler and
BaseQueryHandler, ensuring consistency across all handler types in the CQRS system.
"""

from typing import Optional

from application.base.event_handlers import BaseLoggingEventHandler
from application.events.decorators import event_handler
from domain.base.events import DomainEvent
from domain.base.ports import ErrorHandlingPort, EventPublisherPort, LoggingPort


@event_handler("MachineCreatedEvent")
class MachineCreatedHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle machine creation events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ) -> None:
        """Initialize machine created handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format machine created log message."""
        template_id = getattr(event, "template_id", "unknown")
        instance_type = getattr(event, "instance_type", "unknown")
        availability_zone = getattr(event, "availability_zone", None)

        message = (
            f"Machine created: {getattr(event, 'aggregate_id', 'unknown')} | "
            f"Template: {template_id} | "
            f"Type: {instance_type}"
        )

        if availability_zone:
            message += f" | AZ: {availability_zone}"

        return message


@event_handler("MachineStatusUpdatedEvent")
class MachineStatusUpdatedHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle machine status update events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ) -> None:
        """Initialize machine status updated handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format machine status updated log message."""
        old_status = getattr(event, "old_status", "unknown")
        new_status = getattr(event, "new_status", "unknown")

        return (
            f"Machine status updated: {getattr(event, 'aggregate_id', 'unknown')} | "
            f"Status: {old_status} -> {new_status}"
        )


@event_handler("MachineTerminatedEvent")
class MachineTerminatedHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle machine termination events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ) -> None:
        """Initialize machine terminated handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format machine terminated log message."""
        reason = getattr(event, "termination_reason", "unknown")

        return f"Machine terminated: {getattr(event, 'aggregate_id', 'unknown')} | Reason: {reason}"

    def get_log_level(self, event: DomainEvent) -> str:
        """Get log level - warnings for unexpected terminations."""
        reason = getattr(event, "termination_reason", "unknown")
        if reason in ["user_requested", "scheduled"]:
            return "info"
        else:
            return "warning"


@event_handler("MachineHealthCheckEvent")
class MachineHealthCheckHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle machine health check events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ) -> None:
        """Initialize machine health check handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format machine health check log message."""
        health_status = getattr(event, "health_status", "unknown")
        check_type = getattr(event, "check_type", "unknown")

        return (
            f"Machine health check: {getattr(event, 'aggregate_id', 'unknown')} | "
            f"Type: {check_type} | Status: {health_status}"
        )

    def get_log_level(self, event: DomainEvent) -> str:
        """Get log level - warnings for unhealthy status."""
        health_status = getattr(event, "health_status", "unknown")
        if health_status == "healthy":
            return "debug"  # Reduce noise for healthy checks
        elif health_status == "unhealthy":
            return "warning"
        else:
            return "info"


@event_handler("MachineErrorEvent")
class MachineErrorHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle machine error events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ) -> None:
        """Initialize machine error handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format machine error log message."""
        error_type = getattr(event, "error_type", "unknown")
        error_message = getattr(event, "error_message", "No details available")

        return (
            f"Machine error: {getattr(event, 'aggregate_id', 'unknown')} | "
            f"Type: {error_type} | Message: {error_message}"
        )

    def get_log_level(self, event: DomainEvent) -> str:
        """Get log level - errors for machine errors."""
        return "error"
