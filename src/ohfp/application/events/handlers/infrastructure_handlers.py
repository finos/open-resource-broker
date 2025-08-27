"""
Infrastructure Event Handlers - DRY-compliant handlers using new architecture.

These handlers replace the duplicated code in consolidated_event_handlers.py
with a clean, maintainable architecture following DDD/SOLID/DRY principles.
"""

# Import the new base classes and decorator
from application.base.event_handlers import BaseLoggingEventHandler
from application.events.decorators import event_handler

# Import types - using string imports to avoid circular dependencies
try:
    from domain.base.events import DomainEvent
    from domain.base.ports import LoggingPort
except ImportError:
    # Fallback for testing or when dependencies aren't available
    DomainEvent = object
    LoggingPort = object


@event_handler("DatabaseConnectionEvent")
class DatabaseConnectionHandler(BaseLoggingEventHandler):
    """Handle database connection events - DRY compliant."""

    def format_message(self, event: DomainEvent) -> str:
        """Format database connection message."""
        fields = self.extract_fields(
            event,
            {
                "connection_status": "unknown",
                "database_type": "unknown",
                "connection_time": None,
                "retry_count": 0,
            },
        )

        message = (
            f"Database connection: {fields['connection_status']} | Type: {fields['database_type']}"
        )

        if fields["connection_time"]:
            message += f" | Time: {self.format_duration(fields['connection_time'])}"

        if fields["retry_count"] > 0:
            message += f" | Retries: {fields['retry_count']}"

        return message


@event_handler("CacheOperationEvent")
class CacheOperationHandler(BaseLoggingEventHandler):
    """Handle cache operation events - DRY compliant."""

    def format_message(self, event: DomainEvent) -> str:
        """Format cache operation message."""
        fields = self.extract_fields(
            event,
            {
                "operation": "unknown",
                "cache_key": "unknown",
                "hit_rate": None,
                "operation_time": None,
            },
        )

        message = f"Cache {fields['operation']}: {fields['cache_key']}"

        if fields["hit_rate"] is not None:
            message += f" | Hit rate: {fields['hit_rate']:.1f}%"

        if fields["operation_time"]:
            message += f" | Time: {self.format_duration(fields['operation_time'])}"

        return message
