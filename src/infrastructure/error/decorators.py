"""
Exception handling decorators for consistent error management across layers.

These decorators provide a clean, declarative way to add exception handling
to functions while preserving domain semantics and following SOLID principles.
"""

import inspect
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Type

from src.infrastructure.error.exception_handler import (
    ExceptionContext,
    ExceptionHandler,
    get_exception_handler,
)


def handle_exceptions(
    context: str,
    layer: str = "application",
    preserve_types: Optional[List[Type[Exception]]] = None,
    additional_context: Optional[Dict[str, Any]] = None,
    handler: Optional[ExceptionHandler] = None,
):
    """
    Provide consistent exception handling across all layers.

    This decorator follows the Single Responsibility Principle by delegating
    exception handling to the ExceptionHandler service while keeping business
    logic clean.

    Args:
        context: Operation context (e.g., "template_retrieval", "instance_launch")
        layer: Application layer ("domain", "application", "infrastructure", "interface")
        preserve_types: Additional exception types to preserve without wrapping
        additional_context: Additional context information for logging
        handler: Optional specific exception handler (defaults to global singleton)

    Returns:
        Decorated function with exception handling

    Example:
        @handle_exceptions(context="template_retrieval", layer="application")
        def get_template(self, template_id: str) -> Template:
            return self.template_repository.get_by_id(template_id)
    """

    def decorator(func: Callable) -> Callable:
        """Apply error handling to the function."""
        if inspect.iscoroutinefunction(func):
            # Async wrapper for async functions
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    # Get exception handler (use provided or global singleton)
                    exception_handler = handler or get_exception_handler()

                    # Build rich context
                    context_data = _build_context_data(func, args, kwargs, additional_context)

                    # Create exception context
                    exc_context = ExceptionContext(operation=context, layer=layer, **context_data)

                    # Handle exception
                    handled_exception = exception_handler.handle(e, exc_context)

                    # Re-raise with proper context chain
                    raise handled_exception from e

            return async_wrapper
        else:
            # Sync wrapper for sync functions
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                """Synchronous wrapper with error handling."""
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    # Get exception handler (use provided or global singleton)
                    exception_handler = handler or get_exception_handler()

                    # Build rich context
                    context_data = _build_context_data(func, args, kwargs, additional_context)

                    # Create exception context
                    exc_context = ExceptionContext(operation=context, layer=layer, **context_data)

                    # Handle exception
                    handled_exception = exception_handler.handle(e, exc_context)

                    # Re-raise with proper context chain
                    raise handled_exception from e

            return sync_wrapper

    return decorator


def handle_domain_exceptions(context: str, additional_context: Optional[Dict[str, Any]] = None):
    """
    Specialized decorator for domain layer exception handling.

    This decorator is optimized for domain operations and ensures
    all domain exceptions are preserved with rich context.

    Args:
        context: Domain operation context
        additional_context: Additional domain context

    Example:
        @handle_domain_exceptions(context="template_validation")
        def validate_template(self, template: Template) -> None:
            # Domain validation logic
    """
    return handle_exceptions(context=context, layer="domain", additional_context=additional_context)


def handle_application_exceptions(
    context: str, additional_context: Optional[Dict[str, Any]] = None
):
    """
    Specialized decorator for application layer exception handling.

    This decorator is optimized for application service operations
    and provides consistent error handling for use cases.

    Args:
        context: Application operation context
        additional_context: Additional application context

    Example:
        @handle_application_exceptions(context="request_creation")
        def request_machines(self, template_id: str, count: int):
            # Application logic
    """
    return handle_exceptions(
        context=context, layer="application", additional_context=additional_context
    )


def handle_infrastructure_exceptions(
    context: str, additional_context: Optional[Dict[str, Any]] = None
):
    """
    Specialized decorator for infrastructure layer exception handling.

    This decorator is optimized for infrastructure operations and
    ensures proper wrapping of technical exceptions.

    Args:
        context: Infrastructure operation context
        additional_context: Additional infrastructure context

    Example:
        @handle_infrastructure_exceptions(context="database_query")
        def get_by_id(self, entity_id: str):
            # Infrastructure logic
    """
    return handle_exceptions(
        context=context, layer="infrastructure", additional_context=additional_context
    )


def handle_provider_exceptions(
    context: str, provider: str, additional_context: Optional[Dict[str, Any]] = None
):
    """
    Specialized decorator for provider-specific exception handling.

    This decorator is optimized for cloud provider operations and
    adds provider-specific context to error handling.

    Args:
        context: Provider operation context
        provider: Provider name (e.g., "aws", "provider1", "provider2")
        additional_context: Additional provider context

    Example:
        @handle_provider_exceptions(context="instance_launch", provider="aws")
        def launch_instances(self, request: LaunchRequest):
            # Provider-specific logic
    """
    provider_context = {"provider": provider}
    if additional_context:
        provider_context.update(additional_context)

    return handle_exceptions(
        context=context, layer="infrastructure", additional_context=provider_context
    )


def handle_interface_exceptions(
    context: str,
    interface_type: str = "api",
    additional_context: Optional[Dict[str, Any]] = None,
):
    """
    Specialized decorator for interface layer exception handling.

    This decorator is optimized for interface operations (API, CLI, etc.)
    and ensures proper error responses.

    Args:
        context: Interface operation context
        interface_type: Type of interface ("api", "cli", "web")
        additional_context: Additional interface context

    Example:
        @handle_interface_exceptions(context="get_templates", interface_type="api")
        def get_available_templates(self):
            # Interface logic
    """
    interface_context = {"interface_type": interface_type}
    if additional_context:
        interface_context.update(additional_context)

    return handle_exceptions(
        context=context, layer="interface", additional_context=interface_context
    )


def _build_context_data(
    func: Callable,
    args: tuple,
    kwargs: dict,
    additional_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build rich context data for exception handling.

    This function extracts useful information from the function call
    to provide better debugging context.

    Args:
        func: The decorated function
        args: Function positional arguments
        kwargs: Function keyword arguments
        additional_context: Additional context provided by decorator

    Returns:
        Rich context dictionary
    """
    context_data = {
        "function": func.__name__,
        "module": func.__module__,
        "args_count": len(args),
        "kwargs_keys": list(kwargs.keys()) if kwargs else [],
    }

    # Add function signature information
    try:
        sig = inspect.signature(func)
        context_data["signature"] = str(sig)
    except (ValueError, TypeError):
        # Some functions don't have inspectable signatures
        pass

    # Add class information if this is a method
    if args and hasattr(args[0], "__class__"):
        context_data["class"] = args[0].__class__.__name__

    # Add additional context if provided
    if additional_context:
        context_data.update(additional_context)

    # Extract useful parameter values (be careful with sensitive data)
    if kwargs:
        safe_kwargs = {}
        for key, value in kwargs.items():
            # Only include simple, non-sensitive values
            if (
                isinstance(value, (str, int, float, bool))
                and len(str(value)) < 100
                and not any(
                    sensitive in key.lower()
                    for sensitive in [
                        "password",
                        "secret",
                        "key",
                        "token",
                        "credential",
                    ]
                )
            ):
                safe_kwargs[key] = value

        if safe_kwargs:
            context_data["safe_kwargs"] = safe_kwargs

    return context_data


def handle_rest_exceptions(
    endpoint: str,
    method: str = "GET",
    additional_context: Optional[Dict[str, Any]] = None,
):
    """
    Specialized decorator for REST API exception handling.

    This decorator is optimized for REST endpoint operations and
    ensures proper HTTP status code mapping and response formatting.

    Args:
        endpoint: REST endpoint path (e.g., "/api/v1/templates")
        method: HTTP method (GET, POST, PUT, DELETE)
        additional_context: Additional REST context

    Example:
        @handle_rest_exceptions(endpoint="/api/v1/templates", method="GET")
        async def list_templates(self):
            # REST endpoint logic
    """
    rest_context = {"endpoint": endpoint, "method": method, "layer": "api"}

    if additional_context:
        rest_context.update(additional_context)

    return handle_exceptions(
        context=f"rest_api_{endpoint.replace('/', '_')}_{method.lower()}",
        layer="api",
        additional_context=rest_context,
    )


# Convenience aliases for common patterns
domain_exceptions = handle_domain_exceptions
application_exceptions = handle_application_exceptions
infrastructure_exceptions = handle_infrastructure_exceptions
provider_exceptions = handle_provider_exceptions
interface_exceptions = handle_interface_exceptions
rest_exceptions = handle_rest_exceptions
