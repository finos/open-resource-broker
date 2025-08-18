"""
Request Event Handlers - CQRS-aligned handlers using BaseEventHandler pattern.

These handlers follow the same architectural patterns as BaseCommandHandler and
BaseQueryHandler, ensuring consistency across all handler types in the CQRS system.
"""

from typing import Optional

from application.base.event_handlers import BaseLoggingEventHandler
from application.events.decorators import event_handler
from domain.base.events import DomainEvent
from domain.base.ports import ErrorHandlingPort, EventPublisherPort, LoggingPort


@event_handler("RequestCreatedEvent")
class RequestCreatedHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle request creation events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ):
        """Initialize request created handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format request created log message."""
        template_id = getattr(event, "template_id", "unknown")
        machine_count = getattr(event, "machine_count", 0)

        return (
            f"Request created: {getattr(event, 'aggregate_id', 'unknown')} | "
            f"Template: {template_id} | "
            f"Count: {machine_count}"
        )


@event_handler("RequestStatusUpdatedEvent")
class RequestStatusUpdatedHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle request status update events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ):
        """Initialize request status updated handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format request status updated log message."""
        old_status = getattr(event, "old_status", "unknown")
        new_status = getattr(event, "new_status", "unknown")

        return (
            f"Request status updated: {getattr(event, 'aggregate_id', 'unknown')} | "
            f"Status: {old_status} -> {new_status}"
        )


@event_handler("RequestCompletedEvent")
class RequestCompletedHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle request completion events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ):
        """Initialize request completed handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format request completed log message."""
        duration = getattr(event, "completion_duration", "unknown")
        machines_created = getattr(event, "machines_created", 0)

        return (
            f"Request completed: {getattr(event, 'aggregate_id', 'unknown')} | "
            f"Duration: {duration}s | "
            f"Machines created: {machines_created}"
        )


@event_handler("RequestFailedEvent")
class RequestFailedHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle request failure events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ):
        """Initialize request failed handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format request failed log message."""
        failure_reason = getattr(event, "failure_reason", "unknown")

        return (
            f"Request failed: {getattr(event, 'aggregate_id', 'unknown')} | "
            f"Reason: {failure_reason}"
        )

    def get_log_level(self, event: DomainEvent) -> str:
        """Get log level - error for request failures."""
        return "error"


@event_handler("RequestCancelledEvent")
class RequestCancelledHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle request cancellation events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ):
        """Initialize request cancelled handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format request cancelled log message."""
        cancellation_reason = getattr(event, "cancellation_reason", "user_requested")

        return (
            f"Request cancelled: {getattr(event, 'aggregate_id', 'unknown')} | "
            f"Reason: {cancellation_reason}"
        )

    def get_log_level(self, event: DomainEvent) -> str:
        """Get log level - warning for cancellations."""
        return "warning"


@event_handler("RequestTimeoutEvent")
class RequestTimeoutHandler(BaseLoggingEventHandler[DomainEvent]):
    """Handle request timeout events using BaseEventHandler pattern."""

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ):
        """Initialize request timeout handler."""
        super().__init__(logger, error_handler, event_publisher)

    async def format_log_message(self, event: DomainEvent) -> str:
        """Format request timeout log message."""
        timeout_duration = getattr(event, "timeout_duration", "unknown")

        return (
            f"Request timed out: {getattr(event, 'aggregate_id', 'unknown')} | "
            f"Timeout after: {timeout_duration}s"
        )

    def get_log_level(self, event: DomainEvent) -> str:
        """Get log level - error for timeouts."""
        return "error"
