"""Storage strategy components package with consistent naming."""

from typing import TYPE_CHECKING, Any

# Base interfaces
# Repository components (extracted from repositories)
from .entity_cache import EntityCache, MemoryEntityCache, NoOpEntityCache
from .entity_serializer import BaseEntitySerializer, EntitySerializer
from .event_publisher import (
    EventPublisher,
    InMemoryEventPublisher,
    LoggingEventPublisher,
    NoOpEventPublisher,
)
from .file_manager import FileManager

# Generic components (truly reusable across storage types)
from .lock_manager import LockManager, ReaderWriterLock
from .resource_manager import DataConverter, QueryManager, StorageResourceManager
from .serialization_manager import JSONSerializer, SerializationManager
from .transaction_manager import (
    MemoryTransactionManager,
    NoOpTransactionManager,
    TransactionManager,
)
from .version_manager import MemoryVersionManager, NoOpVersionManager, VersionManager

# SQL-specific components (clearly prefixed) load on first access so the JSON
# and in-memory storage paths do not import SQLAlchemy.
_LAZY_SQL = {
    "SQLConnectionManager": ".sql_connection_manager",
    "SQLQueryBuilder": ".sql_query_builder",
    "SQLSerializer": ".sql_serializer",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_SQL.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name, __name__), name)


if TYPE_CHECKING:
    from .sql_connection_manager import SQLConnectionManager
    from .sql_query_builder import SQLQueryBuilder
    from .sql_serializer import SQLSerializer

__all__: list[str] = [
    # Repository components
    "BaseEntitySerializer",
    "DataConverter",
    "EntityCache",
    "EntitySerializer",
    "EventPublisher",
    "FileManager",
    "InMemoryEventPublisher",
    "JSONSerializer",
    # Generic components
    "LockManager",
    "LoggingEventPublisher",
    "MemoryEntityCache",
    "MemoryTransactionManager",
    "MemoryVersionManager",
    "NoOpEntityCache",
    "NoOpEventPublisher",
    "NoOpTransactionManager",
    "NoOpVersionManager",
    "QueryManager",
    "ReaderWriterLock",
    # SQL components
    "SQLConnectionManager",
    "SQLQueryBuilder",
    "SQLSerializer",
    "SerializationManager",
    "StorageResourceManager",
    "TransactionManager",
    "VersionManager",
]
