"""
Template Event Handlers - DRY-compliant handlers using new architecture.

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


@event_handler("TemplateValidatedEvent")
class TemplateValidatedHandler(BaseLoggingEventHandler):
    """Handle template validation events - DRY compliant."""

    def format_message(self, event: DomainEvent) -> str:
        """Format template validated message."""
        fields = self.extract_fields(
            event,
            {
                "template_name": "unknown",
                "validation_status": "unknown",
                "validation_errors": [],
                "validation_time": None,
            },
        )

        message = (
            f"Template validated: {fields['template_name']} | "
            f"Status: {fields['validation_status']}"
        )

        if fields["validation_errors"]:
            error_count = len(fields["validation_errors"])
            message += f" | Errors: {error_count}"

        if fields["validation_time"]:
            message += f" | Time: {self.format_duration(fields['validation_time'])}"

        return message


@event_handler("TemplateUpdatedEvent")
class TemplateUpdatedHandler(BaseLoggingEventHandler):
    """Handle template update events - DRY compliant."""

    def format_message(self, event: DomainEvent) -> str:
        """Format template updated message."""
        fields = self.extract_fields(
            event,
            {
                "template_name": "unknown",
                "changes": [],
                "updated_by": "system",
                "version": None,
            },
        )

        message = f"Template updated: {fields['template_name']} | " f"By: {fields['updated_by']}"

        if fields["changes"]:
            change_count = len(fields["changes"])
            message += f" | Changes: {change_count}"

        if fields["version"]:
            message += f" | Version: {fields['version']}"

        return message
