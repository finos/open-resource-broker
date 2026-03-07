"""Query handling infrastructure."""

# Import from application ports (clean architecture)
from orb.application.ports.query_bus_port import QueryBusPort

# Import handlers to ensure decorators are registered
from . import (
    cleanup_query_handlers,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    machine_query_handlers,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    request_query_handlers,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    system_handlers,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    template_query_handlers,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

__all__: list[str] = ["QueryBusPort"]
