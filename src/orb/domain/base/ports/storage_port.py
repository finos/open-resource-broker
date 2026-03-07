"""Domain port for storage operations.

This is a composite interface that combines focused storage interfaces.
Clients should depend on the specific focused interfaces they need rather than this fat interface.
"""

from abc import ABC
from typing import Generic, TypeVar

from .storage_lifecycle_port import StorageLifecyclePort
from .storage_reader_port import StorageReaderPort
from .storage_writer_port import StorageWriterPort

T = TypeVar("T")


class StoragePort(
    StorageReaderPort[T], StorageWriterPort[T], StorageLifecyclePort, ABC, Generic[T]
):
    """Composite storage port combining read, write, and lifecycle operations.

    This interface is provided for backward compatibility and for implementations
    that need all storage operations. New code should depend on the focused interfaces:
    - StorageReaderPort: For read-only operations
    - StorageWriterPort: For write-only operations
    - StorageLifecyclePort: For lifecycle management

    This follows ISP by allowing clients to depend on minimal interfaces.
    """

    pass
