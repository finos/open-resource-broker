"""Query handling infrastructure."""

# Import from application ports (clean architecture)
from orb.application.ports.query_bus_port import QueryBusPort

# Import handlers to ensure decorators are registered
from . import (
    cleanup_query_handlers,  # noqa: F401
    machine_query_handlers,  # noqa: F401
    request_query_handlers,  # noqa: F401
    system_handlers,  # noqa: F401
    template_query_handlers,  # noqa: F401
)

__all__: list[str] = ["QueryBusPort"]
