"""Per-call response format selection shared by REST, SDK and MCP.

The configured scheduler decides the response format by default. A caller may
name another registered format for a single call; the strategy is then built
through :class:`SchedulerStrategyFactory`, exactly like the configured one, and
cached by the factory.
"""

from __future__ import annotations

from typing import Any

from orb.domain.base.exceptions import ApplicationError
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.exceptions import RequestValidationError
from orb.infrastructure.scheduler.factory import SchedulerStrategyFactory
from orb.infrastructure.scheduler.registry import get_scheduler_registry
from orb.interface.response_formatting_service import ResponseFormattingService


def select_formatter(container: Any, name: str | None) -> ResponseFormattingService:
    """Return the formatter for the format ``name``, or the configured one if unset.

    Raises ``RequestValidationError`` for a name that is not a registered format
    and ``ApplicationError`` when a registered format cannot be built.
    """
    configured = container.get(ResponseFormattingService)
    if not name:
        return configured

    registry = get_scheduler_registry()
    if not registry.is_registered(name):
        known = ", ".join(sorted(registry.get_registered_types()))
        raise RequestValidationError(f"Unknown scheduler format '{name}'. Registered: {known}")
    if name == container.get(ConfigurationPort).get_scheduler_strategy():
        # The configured format is always served by the container's own service,
        # so a named request for it cannot diverge from the default.
        return configured

    try:
        strategy = container.get(SchedulerStrategyFactory).create_strategy(name, container)
    except Exception as exc:
        raise ApplicationError(f"Scheduler format '{name}' could not be initialised") from exc
    return ResponseFormattingService(strategy)


def invalidate_formats(container: Any) -> None:
    """Drop cached format strategies after the scheduler or provider configuration changed."""
    container.get(SchedulerStrategyFactory).invalidate()
