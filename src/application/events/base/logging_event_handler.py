"""
Logging Event Handler - Specialized base for events that primarily log messages.

This handler is optimized for the common pattern of event handlers that
primarily log structured messages about domain events. It follows the
Template Method pattern and provides consistent message formatting.
"""

from abc import abstractmethod
from typing import Optional

from .event_handler import EventHandler

# Import types - using string imports to avoid circular dependencies
try:
    from domain.base.events import DomainEvent
    from domain.base.ports import LoggingPort
except ImportError:
    # Fallback for testing or when dependencies aren't available
    DomainEvent = object
    LoggingPort = object


class LoggingEventHandler(EventHandler):
    """
    Base class for event handlers that primarily log events.

    This class implements the Template Method pattern where the core
    processing logic is to format and log a message about the event.
    Concrete handlers only need to implement the message formatting logic.

    This eliminates the duplication found in the original consolidated
    event handlers where every handler had the same logging structure
    but different message formats.
    """

    def __init__(self, logger: Optional[LoggingPort] = None):
        """
        Initialize logging event handler.

        Args:
            logger: Logger for event messages
        """
        super().__init__(logger)

    async def process_event(self, event: DomainEvent) -> None:
        """
        Process event by formatting and logging a message.

        This implements the Template Method pattern where the algorithm
        is defined here (format message, then log it) but the specific
        message formatting is delegated to concrete handlers.

        Args:
            event: The domain event to process
        """
        # Format the message (implemented by concrete handlers)
        message = self.format_message(event)

        # Log the message
        if self.logger and message:
            self.logger.info(message)

    @abstractmethod
    def format_message(self, event: DomainEvent) -> str:
        """
        Format the log message for this event.

        This method must be implemented by concrete handlers to define
        how the event should be formatted for logging. This is where
        the specific business logic for each event type is implemented.

        Args:
            event: The domain event to format

        Returns:
            Formatted message string for logging
        """

    def format_basic_message(
        self, event: DomainEvent, action: str, details: Optional[str] = None
    ) -> str:
        """
        Format basic event messages.

        This provides a consistent format for simple event messages
        and can be used by concrete handlers as a starting point.

        Args:
            event: The domain event
            action: The action being performed
            details: Optional additional details

        Returns:
            Formatted message string
        """
        aggregate_id = getattr(event, "aggregate_id", "unknown")
        aggregate_type = getattr(event, "aggregate_type", "unknown")

        message = f"{aggregate_type} {action}: {aggregate_id}"

        if details:
            message += f" | {details}"

        return message

    def format_status_change_message(
        self, event: DomainEvent, old_status: str, new_status: str
    ) -> str:
        """
        Format status change messages.

        Args:
            event: The domain event
            old_status: Previous status
            new_status: New status

        Returns:
            Formatted status change message
        """
        aggregate_id = getattr(event, "aggregate_id", "unknown")
        aggregate_type = getattr(event, "aggregate_type", "unknown")

        status_change = self.format_status_change(old_status, new_status)
        return f"{aggregate_type} status updated: {aggregate_id} | {status_change}"

    def format_error_message(self, event: DomainEvent, error_message: str) -> str:
        """
        Format error event messages.

        Args:
            event: The domain event
            error_message: The error message

        Returns:
            Formatted error message
        """
        aggregate_id = getattr(event, "aggregate_id", "unknown")
        aggregate_type = getattr(event, "aggregate_type", "unknown")

        return f"{aggregate_type} error: {aggregate_id} | {error_message}"
