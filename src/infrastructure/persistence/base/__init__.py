"""Base persistence package."""

from infrastructure.persistence.base.repository import StrategyBasedRepository
from infrastructure.persistence.base.strategy import (
    BaseStorageStrategy,
    StorageStrategy,
)
from infrastructure.persistence.base.unit_of_work import (
    BaseUnitOfWork,
    StrategyUnitOfWork,
)

__all__ = [
    "StrategyBasedRepository",
    "BaseUnitOfWork",
    "StrategyUnitOfWork",
    "StorageStrategy",
    "BaseStorageStrategy",
]
