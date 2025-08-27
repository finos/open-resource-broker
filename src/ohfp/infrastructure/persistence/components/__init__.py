"""Storage strategy components package with consistent naming."""

# Base interfaces
# DynamoDB-specific components (clearly prefixed)
from .dynamodb_client_manager import DynamoDBClientManager
from .dynamodb_converter import DynamoDBConverter
from .dynamodb_transaction_manager import DynamoDBTransactionManager
from .file_manager import FileManager

# Generic components (truly reusable across storage types)
from .lock_manager import LockManager, ReaderWriterLock
from .resource_manager import DataConverter, QueryManager
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

__all__: list[str] = [
    "DataConverter",
    # DynamoDB components
    "DynamoDBClientManager",
    "DynamoDBConverter",
    "DynamoDBTransactionManager",
    "FileManager",
    "JSONSerializer",
    # Generic components
    "LockManager",
    "MemoryTransactionManager",
    "NoOpTransactionManager",
    "QueryManager",
    "ReaderWriterLock",
    # Base interfaces
    "ResourceManager",
    # SQL components
    "SQLConnectionManager",
    "SQLQueryBuilder",
    "SQLSerializer",
    "SerializationManager",
    "TransactionManager",
]
