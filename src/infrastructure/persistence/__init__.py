"""Persistence package."""

# Import only the base classes to avoid circular imports
from infrastructure.persistence.base import (
    BaseUnitOfWork,
    StrategyBasedRepository,
    StrategyUnitOfWork,
)

# Import factory functions but not classes to avoid circular imports
# (No imports needed from repository_factory to avoid circular dependencies)

__all__: list[str] = [
    # Base
    "StrategyBasedRepository",
    "BaseUnitOfWork",
    "StrategyUnitOfWork",
    # Factory functions
]
