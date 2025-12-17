"""Query handling infrastructure."""

# Import from infrastructure layer (the working implementation)
from infrastructure.di.buses import QueryBus

# Import handlers to ensure decorators are registered
from . import handlers  # noqa: F401

__all__: list[str] = ["QueryBus"]
