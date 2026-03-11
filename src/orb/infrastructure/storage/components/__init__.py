"""Storage strategy components package with consistent naming."""

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

# SQL-specific components (clearly prefixed)
from .sql_connection_manager import SQLConnectionManager
from .sql_query_builder import SQLQueryBuilder
from .sql_serializer import SQLSerializer
from .transaction_manager import (
    MemoryTransactionManager,
    NoOpTransactionManager,
    TransactionManager,
)
from .version_manager import MemoryVersionManager, NoOpVersionManager, VersionManager

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
