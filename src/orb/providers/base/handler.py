"""Shared base for provider handlers.

This base consolidates the small, provider-agnostic pieces that every provider
handler duplicates today:

  * :meth:`get_handler_type` — derives the handler type from the concrete class
    name (``FooHandler`` -> ``"foo"``).
  * a thin constructor mixin (:meth:`_init_handler_base`) that stores the
    injected logger and retry knobs on the instance.

Retry *logic* is intentionally NOT lifted here — only the retry configuration
values are stored so subclasses can keep their existing retry behaviour
unchanged.  Consolidating retry execution is deferred to a later change.
"""

from typing import Any, Optional


class ProviderHandlerBase:
    """Base mixin for provider handlers (handler-type + retry-knob storage)."""

    def get_handler_type(self) -> str:
        """Derive the handler/service type from the concrete class name.

        ``EC2FleetHandler`` -> ``"ec2fleet"``; ``PodHandler`` -> ``"pod"``.
        """
        return self.__class__.__name__.replace("Handler", "").lower()

    def _init_handler_base(
        self,
        logger: Optional[Any] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
    ) -> None:
        """Store shared handler dependencies. Call from subclass ``__init__``.

        Args:
            logger: Optional :class:`~orb.domain.base.ports.LoggingPort` instance.
            max_retries: Optional maximum retry attempt count.
            retry_delay: Optional base delay (seconds) between retries.
        """
        self._logger = logger
        self._max_retries = max_retries
        self._retry_delay = retry_delay


__all__ = ["ProviderHandlerBase"]
