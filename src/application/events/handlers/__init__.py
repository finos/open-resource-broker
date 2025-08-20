"""
Event Handlers - New DRY-compliant handler architecture.

This module replaces the old consolidated_event_handlers.py with a clean,
maintainable architecture that eliminates code duplication and follows
DDD/SOLID/DRY principles.

The handlers are automatically registered via the @event_handler decorator
and can be discovered and instantiated by the EventBus.
"""

# Import all handler modules to ensure decorators are executed
from . import (
    infrastructure_handlers,
    machine_handlers,
    request_handlers,
    system_handlers,
    template_handlers,
)

__all__: list[str] = [
    "machine_handlers",
    "request_handlers",
    "system_handlers",
    "infrastructure_handlers",
    "template_handlers",
]
