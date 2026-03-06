"""
SDK middleware system for intercepting and transforming method calls.

Provides a pipeline pattern where middleware can inspect, modify, or
short-circuit SDK method calls before and after execution.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable


class SDKMiddleware(ABC):
    """
    Base class for SDK middleware.

    Subclass and implement `process()` to intercept SDK method calls.

    Example:
        class LoggingMiddleware(SDKMiddleware):
            async def process(self, method_name, args, kwargs, next_handler):
                print(f"Calling {method_name}")
                result = await next_handler(args, kwargs)
                print(f"{method_name} returned: {result}")
                return result
    """

    @abstractmethod
    async def process(
        self,
        method_name: str,
        args: tuple,
        kwargs: dict[str, Any],
        next_handler: Callable,
    ) -> Any:
        """
        Process an SDK method call.

        Args:
            method_name: Name of the SDK method being called
            args: Positional arguments passed to the method
            kwargs: Keyword arguments passed to the method
            next_handler: Callable to invoke the next middleware or the actual method.
                         Call as: await next_handler(args, kwargs)

        Returns:
            The result of the method call (possibly transformed)
        """


def build_middleware_chain(
    middlewares: list[SDKMiddleware],
    method_name: str,
    actual_method: Callable,
) -> Callable:
    """
    Build a callable that chains middlewares around the actual SDK method.

    The first middleware in the list is the outermost (called first).
    """

    async def terminal_handler(args: tuple, kwargs: dict[str, Any]) -> Any:
        return await actual_method(**kwargs)

    handler = terminal_handler
    for mw in reversed(middlewares):

        def make_next(current_mw: SDKMiddleware, next_fn: Callable) -> Callable:
            async def wrapped(args: tuple, kwargs: dict[str, Any]) -> Any:
                return await current_mw.process(method_name, args, kwargs, next_fn)

            return wrapped

        handler = make_next(mw, handler)

    async def entry(**kwargs: Any) -> Any:
        return await handler((), kwargs)

    return entry
