"""Segregated storage interfaces following Interface Segregation Principle."""

from .batch_storage import BatchStorage
from .storage_reader import StorageReader
from .storage_writer import StorageWriter
from .transactional_storage import TransactionalStorage

__all__ = [
    "StorageReader",
    "StorageWriter",
    "BatchStorage",
    "TransactionalStorage",
]
