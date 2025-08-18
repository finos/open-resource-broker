"""
Base Event Handler for CQRS Architecture Consistency.

This module provides BaseEventHandler that follows the same architectural patterns
as BaseCommandHandler and BaseQueryHandler, ensuring consistency across all handler
types in the CQRS system.
"""

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Generic, Optional, TypeVar

from application.interfaces.event_handler import EventHandler
from domain.base.events import DomainEvent
from domain.base.ports import ErrorHandlingPort, EventPublisherPort, LoggingPort

TEvent = TypeVar("TEvent", bound=DomainEvent)


class BaseEventHandler(Generic[TEvent], EventHandler[TEvent], ABC):
    """
    Base event handler following CQRS architecture patterns.

    This class provides the foundation for all event handlers in the system,
    following the same architectural patterns as BaseCommandHandler and BaseQueryHandler:

    - Consistent error handling and logging
    - Template method pattern for event processing
    - Performance monitoring and metrics
    - Dependency injection support
    - Professional exception handling

    Architecture Alignment:
    - EventHandler (interface) -> BaseEventHandler (implementation)
    - Same pattern as CommandHandler -> BaseCommandHandler
    - Same pattern as QueryHandler -> BaseQueryHandler
    """

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
        error_handler: Optional[ErrorHandlingPort] = None,
        event_publisher: Optional[EventPublisherPort] = None,
    ):
        """
        Initialize base event handler.

        Args:
            logger: Logging port for operation logging
            error_handler: Error handling port for exception management
            event_publisher: Event publisher for cascading events
        """
        self.logger = logger
        self.error_handler = error_handler
        self.event_publisher = event_publisher
        self._metrics: Dict[str, Any] = {}

    async def handle(self, event: TEvent) -> None:
        """
        Handle event with monitoring and error management.

        Template method that provides consistent event handling
        across all event handlers, following the same pattern
        as BaseCommandHandler and BaseQueryHandler.
        """
        start_time = time.time()
        event_type = event.__class__.__name__

        try:
            # Log event processing start
            if self.logger:
                self.logger.info("Processing event: %s", event_type)

            # Validate event
            await self.validate_event(event)

            # Execute event processing
            await self.execute_event(event)

            # Record success metrics
            duration = time.time() - start_time
            self._record_success_metrics(event_type, duration)

            if self.logger:
                self.logger.info("Event processed successfully: %s (%ss)", event_type, duration:.3f)

        except Exception as e:
            # Record failure metrics
            duration = time.time() - start_time
            self._record_failure_metrics(event_type, duration, e)

            # Handle error through error handler
            if self.error_handler:
                await self.error_handler.handle_error(
                    e,
                    {
                        "event_type": event_type,
                        "event_data": getattr(event, "__dict__", {}),
                        "duration": duration,
                    },
                )

            if self.logger:
                self.logger.error("Event processing failed: %s - %s", event_type, str(e))

            # Re-raise for upstream handling
            raise

    async def validate_event(self, event: TEvent) -> None:
        """
        Validate event before processing.

        Override this method to implement event-specific validation.
        Default implementation performs basic validation.

        Args:
            event: Event to validate

        Raises:
            ValidationError: If event is invalid
        """
        if not event:
            raise ValueError("Event cannot be None")

        if not hasattr(event, "event_id"):
            raise ValueError("Event must have event_id")

        if not hasattr(event, "event_type"):
            raise ValueError("Event must have event_type")

    @abstractmethod
    async def execute_event(self, event: TEvent) -> None:
        """
        Execute event processing logic.

        This is the core method that concrete event handlers must implement.
        It contains the specific business logic for handling the event.

        Args:
            event: Event to process

        Raises:
            Any exception that occurs during event processing
        """

    async def publish_cascading_events(self, events: list[DomainEvent]) -> None:
        """
        Publish cascading events that result from processing this event.

        Args:
            events: List of events to publish
        """
        if self.event_publisher and events:
            for cascading_event in events:
                try:
                    await self.event_publisher.publish(cascading_event)
                    if self.logger:
                        self.logger.debug(
                            "Published cascading event: %s",  cascading_event.__class__.__name__
                        )
                except Exception as e:
                    if self.logger:
                        self.logger.error("Failed to publish cascading event: %s", e)

    def _record_success_metrics(self, event_type: str, duration: float) -> None:
        """Record success metrics for monitoring."""
        if event_type not in self._metrics:
            self._metrics[event_type] = {
                "success_count": 0,
                "failure_count": 0,
                "total_duration": 0.0,
                "avg_duration": 0.0,
            }

        metrics = self._metrics[event_type]
        metrics["success_count"] += 1
        metrics["total_duration"] += duration
        total_count = metrics["success_count"] + metrics["failure_count"]
        metrics["avg_duration"] = (
            metrics["total_duration"] / total_count if total_count > 0 else 0.0
        )

    def _record_failure_metrics(self, event_type: str, duration: float, error: Exception) -> None:
        """Record failure metrics for monitoring."""
        if event_type not in self._metrics:
            self._metrics[event_type] = {
                "success_count": 0,
                "failure_count": 0,
                "total_duration": 0.0,
                "avg_duration": 0.0,
                "last_error": None,
            }

        metrics = self._metrics[event_type]
        metrics["failure_count"] += 1
        metrics["total_duration"] += duration
        metrics["last_error"] = str(error)
        total_count = metrics["success_count"] + metrics["failure_count"]
        metrics["avg_duration"] = (
            metrics["total_duration"] / total_count if total_count > 0 else 0.0
        )

    def get_metrics(self) -> Dict[str, Any]:
        """Get handler performance metrics."""
        return self._metrics.copy()


class BaseLoggingEventHandler(BaseEventHandler[TEvent]):
    """
    Base event handler specialized for logging events.

    This handler is optimized for the common pattern of event handlers that
    primarily log structured messages about domain events. It extends
    BaseEventHandler while providing logging-specific functionality.
    """

    async def execute_event(self, event: TEvent) -> None:
        """
        Execute event by formatting and logging a message.

        This implements the Template Method pattern where the algorithm
        is defined here (format message, then log it) but the specific
        message formatting is delegated to concrete handlers.
        """
        # Get formatted message from concrete handler
        message = await self.format_log_message(event)

        # Log the message at appropriate level
        log_level = self.get_log_level(event)

        if self.logger:
            if log_level == "debug":
                self.logger.debug(message)
            elif log_level == "info":
                self.logger.info(message)
            elif log_level == "warning":
                self.logger.warning(message)
            elif log_level == "error":
                self.logger.error(message)
            else:
                self.logger.info(message)  # Default to info

    @abstractmethod
    async def format_log_message(self, event: TEvent) -> str:
        """
        Format the log message for this event.

        Concrete handlers must implement this method to provide
        event-specific message formatting.

        Args:
            event: Event to format message for

        Returns:
            Formatted log message
        """

    def get_log_level(self, event: TEvent) -> str:
        """
        Get the appropriate log level for this event.

        Override this method to customize log levels for different events.
        Default implementation returns 'info'.

        Args:
            event: Event to determine log level for

        Returns:
            Log level ('debug', 'info', 'warning', 'error')
        """
        return "info"
