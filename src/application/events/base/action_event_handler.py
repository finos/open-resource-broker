"""
Action Event Handler - Specialized base for events that perform actions.

This handler is for event handlers that need to perform actual business
actions in response to events, not just log them. Examples include
sending notifications, updating caches, triggering workflows, etc.
"""

from abc import abstractmethod
from typing import Any, Dict, Optional

from .event_handler import EventHandler

# Import types - using string imports to avoid circular dependencies
try:
    from src.domain.base.events import DomainEvent
    from src.domain.base.ports import LoggingPort
except ImportError:
    # Fallback for testing or when dependencies aren't available
    DomainEvent = object
    LoggingPort = object


class ActionEventHandler(EventHandler):
    """
    Base class for event handlers that perform actions.

    This class is for handlers that need to do more than just log events.
    They might send notifications, update caches, trigger workflows,
    call external services, etc.

    The Template Method pattern is used where the core processing logic
    validates the event, executes the action, and handles results.
    """

    def __init__(self, logger: Optional[LoggingPort] = None):
        """
        Initialize action event handler.

        Args:
            logger: Logger for action results and errors
        """
        super().__init__(logger)

    async def process_event(self, event: DomainEvent) -> None:
        """
        Process event by executing an action.

        This implements the Template Method pattern:
        1. Validate the event can be processed
        2. Execute the specific action
        3. Handle the action result

        Args:
            event: The domain event to process
        """
        # 1. Validate event (can be overridden)
        if not await self.can_handle_event(event):
            if self.logger:
                self.logger.debug(f"Skipping event {event.event_type} - cannot handle")
            return

        # 2. Execute the action (implemented by concrete handlers)
        result = await self.execute_action(event)

        # 3. Handle the result (can be overridden)
        await self.handle_action_result(event, result)

    async def can_handle_event(self, event: DomainEvent) -> bool:
        """
        Check if this handler can process the given event.

        Override this method to add validation logic for whether
        the event should be processed. For example, checking if
        required fields are present, if the event is not too old, etc.

        Args:
            event: The domain event to validate

        Returns:
            True if the event can be handled, False otherwise
        """
        return True  # Default: handle all events

    @abstractmethod
    async def execute_action(self, event: DomainEvent) -> Any:
        """
        Execute the specific action for this event.

        This method must be implemented by concrete handlers to define
        what action should be taken in response to the event.

        Args:
            event: The domain event that triggered the action

        Returns:
            Result of the action (can be any type)
        """

    async def handle_action_result(self, event: DomainEvent, result: Any) -> None:
        """
        Handle the result of the action execution.

        Override this method to add custom result handling logic.
        For example, logging success/failure, updating metrics, etc.

        Args:
            event: The domain event that was processed
            result: The result returned by execute_action()
        """
        if self.logger:
            event_type = getattr(event, "event_type", "unknown")
            aggregate_id = getattr(event, "aggregate_id", "unknown")
            self.logger.debug(f"Action completed for {event_type} ({aggregate_id})")

    async def _handle_error(self, event: DomainEvent, error: Exception, duration: float) -> None:
        """
        Comprehensive error handling for action handlers.

        This extends the base error handling to include action-specific
        error handling like rollback operations, compensation actions, etc.

        Args:
            event: The domain event that failed processing
            error: The exception that occurred
            duration: How long processing took before failing
        """
        # Call base error handling first
        await super()._handle_error(event, error, duration)

        # Add action-specific error handling
        await self.handle_action_error(event, error)

    async def handle_action_error(self, event: DomainEvent, error: Exception) -> None:
        """
        Handle errors specific to action execution.

        Override this method to add custom error handling logic
        such as rollback operations, compensation actions, or
        triggering error workflows.

        Args:
            event: The domain event that failed processing
            error: The exception that occurred
        """
        if self.logger:
            event_type = getattr(event, "event_type", "unknown")
            aggregate_id = getattr(event, "aggregate_id", "unknown")
            self.logger.error(
                f"Action failed for {event_type} ({aggregate_id}): {str(error)}")

    def extract_action_data(self, event: DomainEvent) -> Dict[str, Any]:
        """
        Extract data needed for action execution.

        This is a convenience method that can be overridden by concrete
        handlers to extract and validate the data they need from the event.

        Args:
            event: The domain event to extract data from

        Returns:
            Dictionary of extracted data
        """
        return {
            "event_id": getattr(event, "event_id", None),
            "aggregate_id": getattr(event, "aggregate_id", None),
            "aggregate_type": getattr(event, "aggregate_type", None),
            "occurred_at": getattr(event, "occurred_at", None),
        }
