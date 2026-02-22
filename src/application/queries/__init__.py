"""Query handling infrastructure."""

# Import from application ports (clean architecture)
from application.ports.query_bus_port import QueryBusPort

# Import handlers to ensure decorators are registered
from . import handlers  # noqa: F401
from . import system_handlers  # noqa: F401
from . import cleanup_query_handlers  # noqa: F401

__all__: list[str] = ["QueryBusPort"]
