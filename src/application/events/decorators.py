"""
Event Handler Decorators - CQRS-aligned event handler registration.

Following the same pattern as CommandHandler and QueryHandler decorators.
"""

from typing import TYPE_CHECKING, Dict, Type

if TYPE_CHECKING:
    from application.events.base.event_handler import EventHandler


class EventHandlerRegistry:
    """Registry for automatic event handler discovery - CQRS pattern."""

    _handlers: Dict[str, Type["EventHandler"]] = {}

    @classmethod
    def register(cls, event_type: str):
        """
        Register event handlers.

        CQRS event handlers use ONLY this decorator - Handler Discovery System
        automatically registers them in the DI container. Do NOT use @injectable
        with CQRS event handlers.

        Usage:
            @event_handler("MachineCreatedEvent")  # ONLY decorator needed
            class MachineCreatedHandler(BaseLoggingEventHandler[DomainEvent]):
                # Handler Discovery System automatically registers this in DI
                def format_message(self, event: DomainEvent) -> str:
                    return f"Machine created: {event.aggregate_id}"

        For non-CQRS services, use @injectable:
            @injectable  # For regular services, NOT handlers
            class MyService:
                ...

        Args:
            event_type: The event type this handler processes
        """

        def decorator(handler_class: Type["EventHandler"]):
            """Apply event handler registration to the class."""
            cls._handlers[event_type] = handler_class
            # Add event_type as class attribute for introspection
            handler_class._event_type = event_type
            return handler_class

        return decorator

    @classmethod
    def get_handlers(cls) -> Dict[str, Type["EventHandler"]]:
        """Get all registered handlers."""
        return cls._handlers.copy()

    @classmethod
    def get_handler_for_event(cls, event_type: str) -> Type["EventHandler"]:
        """Get handler class for specific event type."""
        return cls._handlers.get(event_type)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered handlers (for testing)."""
        cls._handlers.clear()


# Convenient decorator alias following CQRS naming convention
def event_handler(event_type: str):
    """
    Register event handlers - CQRS style.

    This follows the same pattern as @command_handler and @query_handler
    decorators used elsewhere in the CQRS architecture.

    Args:
        event_type: The event type this handler processes

    Example:
        @event_handler("MachineCreatedEvent")
        class MachineCreatedHandler(LoggingEventHandler):
            def format_message(self, event: DomainEvent) -> str:
                return f"Machine created: {event.aggregate_id}"
    """
    return EventHandlerRegistry.register(event_type)


# Export for easy imports
__all__ = ["event_handler", "EventHandlerRegistry"]
