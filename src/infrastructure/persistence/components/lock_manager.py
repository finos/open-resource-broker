"""Locking components for thread-safe storage operations."""

import threading
from contextlib import contextmanager

from infrastructure.logging.logger import get_logger


class ReaderWriterLock:
    """
    Reader-writer lock implementation.

    Allows multiple readers to access the resource simultaneously,
    but only one writer at a time, with no readers present.
    """

    def __init__(self):
        """Initialize reader-writer lock."""
        self._readers = 0
        self._writers = 0
        self._read_ready = threading.Condition(threading.RLock())
        self._write_ready = threading.Condition(threading.RLock())
        self.logger = get_logger(__name__)

    def acquire_read(self):
        """Acquire read lock."""
        with self._read_ready:
            while self._writers > 0:
                self._read_ready.wait()
            self._readers += 1
            self.logger.debug("Read lock acquired. Active readers: %s", self._readers)

    def release_read(self):
        """Release read lock."""
        with self._read_ready:
            self._readers -= 1
            self.logger.debug("Read lock released. Active readers: %s", self._readers)
            if self._readers == 0:
                self._read_ready.notifyAll()

    def acquire_write(self):
        """Acquire write lock."""
        with self._write_ready:
            while self._writers > 0 or self._readers > 0:
                self._write_ready.wait()
            self._writers += 1
            self.logger.debug("Write lock acquired")

    def release_write(self):
        """Release write lock."""
        with self._write_ready:
            self._writers -= 1
            self.logger.debug("Write lock released")
            self._write_ready.notifyAll()
            with self._read_ready:
                self._read_ready.notifyAll()

    @contextmanager
    def read_lock(self):
        """Context manager for read lock."""
        self.acquire_read()
        try:
            yield
        finally:
            self.release_read()

    @contextmanager
    def write_lock(self):
        """Context manager for write lock."""
        self.acquire_write()
        try:
            yield
        finally:
            self.release_write()


class LockManager:
    """
    High-level locking manager for storage operations.

    Provides different locking strategies based on storage type and requirements.
    """

    def __init__(self, lock_type: str = "reader_writer"):
        """
        Initialize lock manager.

        Args:
            lock_type: Type of lock to use ("reader_writer", "simple", "none")
        """
        self.lock_type = lock_type
        self.logger = get_logger(__name__)

        if lock_type == "reader_writer":
            self._lock = ReaderWriterLock()
        elif lock_type == "simple":
            self._lock = threading.RLock()
        elif lock_type == "none":
            self._lock = None
        else:
            raise ValueError(f"Unknown lock type: {lock_type}") from e

    @contextmanager
    def read_lock(self):
        """Acquire read lock for read operations."""
        if self.lock_type == "reader_writer":
            with self._lock.read_lock():
                yield
        elif self.lock_type == "simple":
            with self._lock:
                yield
        else:  # none
            yield

    @contextmanager
    def write_lock(self):
        """Acquire write lock for write operations."""
        if self.lock_type == "reader_writer":
            with self._lock.write_lock():
                yield
        elif self.lock_type == "simple":
            with self._lock:
                yield
        else:  # none
            yield

    @contextmanager
    def exclusive_lock(self):
        """Acquire exclusive lock (alias for write_lock)."""
        with self.write_lock():
            yield
