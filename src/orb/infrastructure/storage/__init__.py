"""Storage package."""

# Import only the base classes to avoid circular imports
from orb.infrastructure.storage.base import (
    BaseUnitOfWork,
    StrategyBasedRepository,
    StrategyUnitOfWork,
)
from orb.infrastructure.storage.factory import StorageStrategyFactory

# Registry and factory are now in this module
from orb.infrastructure.storage.registry import StorageRegistry

__all__: list[str] = [
    "BaseUnitOfWork",
    # Base
    "StrategyBasedRepository",
    "StrategyUnitOfWork",
    # Registry and Factory
    "StorageRegistry",
    "StorageStrategyFactory",
]
