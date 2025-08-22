"""
Base Event Handler - Template Method Pattern for event processing.

This follows the same architectural patterns as CommandHandler and QueryHandler
in the CQRS system, providing consistent event handling with clear separation
of concerns and DRY compliance.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

# Import types - using string imports to avoid circular dependencies
try:
    from domain.base.events import DomainEvent
    from domain.base.ports import LoggingPort
except ImportError:
    # Fallback for testing or when dependencies aren't available
    DomainEvent = Any
    LoggingPort = Any


class EventHandler(ABC):
    """
    Base event handler following Template Method pattern.

    This provides the foundation for all event handlers in the system,
    ensuring consistent behavior, error handling, and logging while
    allowing specific implementations to focus on their core logic.

    Architecture:
    - Template Method Pattern: handle() method defines the algorithm
    - Strategy Pattern: Concrete handlers implement process_event()
    - Dependency Injection: Logger and other dependencies injected
    - Error Handling: Consistent error handling and retry logic
    """

    def __init__(self, logger: Optional[LoggingPort] = None) -> None:
        """
        Initialize event handler.

        Args:
            logger: Logger for event processing messages
        """
        self.logger = logger
        self.retry_count = 3
        self.retry_delay = 1.0  # seconds

    async def handle(self, event: DomainEvent) -> None:
        """
        Template method for event handling.

        This method defines the standard algorithm for processing events:
        1. Pre-processing (logging, validation, etc.)
        2. Core processing (implemented by subclasses)
        3. Post-processing (metrics, cleanup, etc.)
        4. Error handling (retry logic, dead letter queue)

        Args:
            event: The domain event to process
        """
        start_time = time.time()
        event_id = getattr(event, "event_id", "unknown")

        try:
            # 1. Pre-processing
            await self._pre_process(event)

            # 2. Core processing (implemented by subclasses)
            await self._process_with_retry(event)

            # 3. Post-processing
            await self._post_process(event)

            # Log successful processing
            duration = time.time() - start_time
            if self.logger:
                self.logger.debug(
                    "Event processed successfully: %s (ID: %s) in %.3fs",
                    event.event_type,
                    event_id,
                    duration,
                )

        except Exception as e:
            duration = time.time() - start_time
            await self._handle_error(event, e, duration)
            # Re-raise to allow upstream error handling
            raise

    @abstractmethod
    async def process_event(self, event: DomainEvent) -> None:
        """
        Process the specific event logic.

        This method must be implemented by concrete event handlers
        to define the specific processing logic for their event type.

        Args:
            event: The domain event to process
        """

    async def _pre_process(self, event: DomainEvent) -> None:
        """
        Perform common pre-processing logic.

        Override in subclasses for specific pre-processing needs.

        Args:
            event: The domain event to pre-process
        """
        if self.logger:
            self.logger.debug("Processing event: %s", event.event_type)

    @abstractmethod
    async def _post_process(self, event: DomainEvent) -> None:
        """
        Perform common post-processing logic.

        Override in subclasses for specific post-processing needs.
        This is where cross-cutting concerns like metrics collection,
        audit logging, or cache invalidation would be handled.

        Args:
            event: The domain event that was processed
        """
        # Future: Add metrics collection, audit logging, etc.

    async def _process_with_retry(self, event: DomainEvent):
        """
        Process event with retry logic.

        Args:
            event: The domain event to process
        """
        last_exception = None

        for attempt in range(self.retry_count):
            try:
                await self.process_event(event)
                return  # Success, exit retry loop

            except Exception as e:
                last_exception = e

                if attempt < self.retry_count - 1:
                    # Not the last attempt, wait and retry
                    if self.logger:
                        self.logger.warning(
                            "Event processing failed (attempt %s/%s): %s - %s",
                            attempt + 1,
                            self.retry_count,
                            event.event_type,
                            str(e),
                        )
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                else:
                    # Last attempt failed, will re-raise
                    break

        # All retries failed
        if last_exception:
            raise last_exception

    async def _handle_error(self, event: DomainEvent, error: Exception, duration: float) -> None:
        """
        Handle event processing errors.

        Override in subclasses for specific error handling needs.

        Args:
            event: The domain event that failed processing
            error: The exception that occurred
            duration: How long processing took before failing
        """
        event_id = getattr(event, "event_id", "unknown")

        if self.logger:
            self.logger.error(
                "Event processing failed: %s (ID: %s) after %.3fs - %s",
                event.event_type,
                event_id,
                duration,
                str(error),
            )

        # Future: Send to dead letter queue, trigger alerts, etc.
        await self._send_to_dead_letter_queue(event, error)

    async def _send_to_dead_letter_queue(self, event: DomainEvent, error: Exception) -> None:
        """
        Send failed event to dead letter queue.

        Override in subclasses for specific dead letter handling.

        Args:
            event: The domain event that failed processing
            error: The exception that occurred
        """
        if self.logger:
            self.logger.error(
                "Event sent to dead letter queue: %s - %s", event.event_type, str(error)
            )
        # Future: Implement actual dead letter queue integration

    def extract_fields(self, event: DomainEvent, field_mapping: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract and map fields from event data.

        Utility method for consistent event field extraction across handlers.

        Args:
            event: The domain event to extract fields from
            field_mapping: Dictionary of field names to default values

        Returns:
            Dictionary of extracted field values
        """
        result = {}

        for field_name, default_value in field_mapping.items():
            # Try to get field from event attributes first
            if hasattr(event, field_name):
                result[field_name] = getattr(event, field_name)
            # Then try event data if it exists
            elif hasattr(event, "data") and isinstance(event.data, dict):
                result[field_name] = event.data.get(field_name, default_value)
            else:
                result[field_name] = default_value

        return result

    def format_duration(self, duration_ms: float) -> str:
        """
        Format duration for consistent display.

        Args:
            duration_ms: Duration in milliseconds

        Returns:
            Formatted duration string
        """
        if duration_ms < 1000:
            return f"{duration_ms:.1f}ms"
        else:
            return f"{duration_ms / 1000:.2f}s"

    def format_status_change(
        self, old_status: str, new_status: str, reason: Optional[str] = None
    ) -> str:
        """
        Format status change for consistent display.

        Args:
            old_status: Previous status
            new_status: New status
            reason: Optional reason for change

        Returns:
            Formatted status change string
        """
        message = f"{old_status} -> {new_status}"
        if reason:
            message += f" (Reason: {reason})"
        return message
