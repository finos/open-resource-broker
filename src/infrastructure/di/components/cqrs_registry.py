"""CQRS handler registration management for DI container."""

import logging
import threading
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


class CQRSHandlerRegistry:
    """Manages CQRS handler registration for dependency injection."""

    def __init__(self):
        """Initialize the instance."""
        self._command_handlers: Dict[Type, Type] = {}
        self._query_handlers: Dict[Type, Type] = {}
        self._event_handlers: Dict[Type, List[Type]] = {}
        self._lock = threading.RLock()

    def register_command_handler(self, command_type: Type, handler_type: Type) -> None:
        """Register a command handler."""
        with self._lock:
            self._command_handlers[command_type] = handler_type
            logger.debug(
                "Registered command handler: %s -> %s", command_type.__name__, handler_type.__name__
            )

    def register_query_handler(self, query_type: Type, handler_type: Type) -> None:
        """Register a query handler."""
        with self._lock:
            self._query_handlers[query_type] = handler_type
            logger.debug(
                "Registered query handler: %s -> %s", query_type.__name__, handler_type.__name__
            )

    def register_event_handler(self, event_type: Type, handler_type: Type) -> None:
        """Register an event handler."""
        with self._lock:
            if event_type not in self._event_handlers:
                self._event_handlers[event_type] = []

            if handler_type not in self._event_handlers[event_type]:
                self._event_handlers[event_type].append(handler_type)
                logger.debug(
                    "Registered event handler: %s -> %s", event_type.__name__, handler_type.__name__
                )

    def get_command_handler_type(self, command_type: Type) -> Optional[Type]:
        """Get command handler type for a command."""
        with self._lock:
            return self._command_handlers.get(command_type)

    def get_query_handler_type(self, query_type: Type) -> Optional[Type]:
        """Get query handler type for a query."""
        with self._lock:
            return self._query_handlers.get(query_type)

    def get_event_handler_types(self, event_type: Type) -> List[Type]:
        """Get event handler types for an event."""
        with self._lock:
            return self._event_handlers.get(event_type, []).copy()

    def has_command_handler(self, command_type: Type) -> bool:
        """Check if command handler is registered."""
        with self._lock:
            return command_type in self._command_handlers

    def has_query_handler(self, query_type: Type) -> bool:
        """Check if query handler is registered."""
        with self._lock:
            return query_type in self._query_handlers

    def has_event_handlers(self, event_type: Type) -> bool:
        """Check if event handlers are registered."""
        with self._lock:
            return event_type in self._event_handlers and len(self._event_handlers[event_type]) > 0

    def clear(self) -> None:
        """Clear all CQRS handler registrations."""
        with self._lock:
            self._command_handlers.clear()
            self._query_handlers.clear()
            self._event_handlers.clear()
            logger.info("CQRS handler registry cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Get CQRS registry statistics."""
        with self._lock:
            return {
                "command_handlers": len(self._command_handlers),
                "query_handlers": len(self._query_handlers),
                "event_types": len(self._event_handlers),
                "total_event_handlers": sum(
                    len(handlers) for handlers in self._event_handlers.values()
                ),
            }
