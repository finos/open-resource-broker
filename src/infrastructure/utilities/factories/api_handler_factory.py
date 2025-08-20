"""
API Handler Factory - Registration-based factory pattern.

This factory implements a registration-based pattern that eliminates
architectural violations by allowing handlers to register themselves
rather than the factory importing them directly.

Clean Architecture Compliance:
- Infrastructure layer provides factory mechanism
- Interface layer handlers register themselves
- No Infrastructure -> Interface dependencies
"""

import importlib
from typing import TYPE_CHECKING, Dict, Optional, Type

# Domain imports (Clean Architecture compliant)
from domain.base.dependency_injection import injectable

# Infrastructure imports
from infrastructure.logging.logger import get_logger

if TYPE_CHECKING:
    from application.service import ApplicationService

logger = get_logger(__name__)


@injectable
class APIHandlerFactory:
    """
    Registration-based API handler factory.

    This factory uses a registration pattern where handlers register themselves,
    eliminating the need for the factory to import handler classes directly.
    This maintains Clean Architecture by avoiding Infrastructure -> Interface dependencies.
    """

    _handlers: Dict[str, Type] = {}

    @classmethod
    def register_handler(cls, name: str, handler_class: Type) -> None:
        """
        Register a handler class.

        Args:
            name: Handler name/identifier
            handler_class: Handler class to register
        """
        cls._handlers[name] = handler_class
        logger.debug("Registered API handler: %s -> %s", name, handler_class.__name__)

    @classmethod
    def create_handler(cls, name: str, app_service: Optional["ApplicationService"] = None) -> None:
        """
        Create a handler instance.

        Args:
            name: Handler name
            app_service: Optional application service

        Returns:
            Handler instance

        Raises:
            ValueError: If handler not found
        """
        # Check if handler is registered
        if name not in cls._handlers:
            # Try to load handler dynamically (without direct imports)
            cls._try_dynamic_load(name)

            if name not in cls._handlers:
                available_handlers = list(cls._handlers.keys())
                raise ValueError(
                    f"Handler not found: {name}. " f"Available handlers: {available_handlers}"
                )

        handler_class = cls._handlers[name]

        # Create handler instance
        try:
            if app_service:
                # Try to create with app_service if provided
                return handler_class(app_service)
            else:
                # Create with default constructor
                return handler_class()
        except Exception as e:
            logger.error("Failed to create handler %s: %s", name, e)
            raise ValueError(f"Failed to create handler {name}: {e}")

    @classmethod
    def _try_dynamic_load(cls, name: str) -> None:
        """
        Try to dynamically load a handler without direct imports.

        This method attempts to load handlers by convention without
        creating architectural violations.

        Args:
            name: Handler name to load
        """
        # Handler name to module mapping (by convention)
        handler_mappings = {
            "get_available_templates": "src.api.handlers.get_available_templates_handler",
            "request_machines": "src.api.handlers.request_machines_handler",
            "get_request_status": "src.api.handlers.get_request_status_handler",
            "get_return_requests": "src.api.handlers.get_return_requests_handler",
            "request_return_machines": "src.api.handlers.request_return_machines_handler",
        }

        # Handler name to class name mapping (by convention)
        class_mappings = {
            "get_available_templates": "GetAvailableTemplatesRESTHandler",
            "request_machines": "RequestMachinesRESTHandler",
            "get_request_status": "GetRequestStatusRESTHandler",
            "get_return_requests": "GetReturnRequestsRESTHandler",
            "request_return_machines": "RequestReturnMachinesRESTHandler",
        }

        if name in handler_mappings:
            try:
                module_name = handler_mappings[name]
                class_name = class_mappings[name]

                # Dynamic import without creating architectural violation
                module = importlib.import_module(module_name)
                handler_class = getattr(module, class_name)

                # Register the dynamically loaded handler
                cls.register_handler(name, handler_class)
                logger.debug("Dynamically loaded handler: %s", name)

            except (ImportError, AttributeError) as e:
                logger.warning("Failed to dynamically load handler %s: %s", name, e)

    @classmethod
    def get_registered_handlers(cls) -> Dict[str, Type]:
        """
        Get all registered handlers.

        Returns:
            Dictionary of registered handlers
        """
        return cls._handlers.copy()

    @classmethod
    def is_handler_registered(cls, name: str) -> bool:
        """
        Check if a handler is registered.

        Args:
            name: Handler name to check

        Returns:
            True if handler is registered, False otherwise
        """
        return name in cls._handlers

    @classmethod
    def unregister_handler(cls, name: str) -> bool:
        """
        Unregister a handler.

        Args:
            name: Handler name to unregister

        Returns:
            True if handler was unregistered, False if not found
        """
        if name in cls._handlers:
            del cls._handlers[name]
            logger.debug("Unregistered API handler: %s", name)
            return True
        return False

    @classmethod
    def clear_handlers(cls) -> None:
        """Clear all registered handlers."""
        cls._handlers.clear()
        logger.debug("Cleared all registered API handlers")


# Factory instance for global access
api_handler_factory = APIHandlerFactory()


def get_api_handler_factory() -> APIHandlerFactory:
    """
    Get the global API handler factory instance.

    Returns:
        APIHandlerFactory instance
    """
    return api_handler_factory
