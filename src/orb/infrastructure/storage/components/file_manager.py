"""File management components for file-based storage operations."""

import hashlib
import os
import shutil
import tempfile
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from orb.infrastructure.logging.logger import get_logger

# fcntl is POSIX-only (Linux, macOS). On Windows it is unavailable.
# If missing, we degrade gracefully to no cross-process file locking and emit
# a one-time warning so operators know the protection is absent.
try:
    import fcntl as _fcntl

    _FCNTL_AVAILABLE = True
except ImportError:  # pragma: no cover — Windows-only path
    _fcntl = None  # type: ignore[assignment]
    _FCNTL_AVAILABLE = False
    warnings.warn(
        "fcntl is not available on this platform; JSON storage cross-process "
        "write serialization is disabled. Concurrent CLI invocations targeting "
        "the same JSON file may race. Use PostgreSQL storage for multi-process "
        "deployments.",
        RuntimeWarning,
        stacklevel=2,
    )


class FileManager:
    """
    File operations manager for atomic file operations, backups, and integrity checking.

    Handles all file I/O operations with safety features like atomic writes,
    backup management, and integrity verification.
    """

    def __init__(
        self,
        file_path: str,
        create_dirs: bool = True,
        backup_count: int = 5,
        backup_enabled: bool = True,
    ) -> None:
        """
        Initialize file manager.

        Args:
            file_path: Path to the main data file
            create_dirs: Whether to create parent directories
            backup_count: Number of backup files to keep
            backup_enabled: Whether to create backups
        """
        self.file_path = Path(file_path)
        self.backup_count = backup_count
        self.backup_enabled = backup_enabled
        self.backup_dir = self.file_path.parent / "backups"
        self.logger = get_logger(__name__)

        # Create parent directories if needed
        if create_dirs and not self.file_path.parent.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self.logger.info("Created directory: %s", self.file_path.parent)

        if backup_enabled and create_dirs:
            self.backup_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def exclusive_write_lock(self) -> Generator[None, None, None]:
        """Acquire an exclusive cross-process file lock for the duration of a
        read-modify-write cycle.

        Uses ``fcntl.flock(LOCK_EX)`` on a sibling ``.lock`` file so that the
        lock descriptor is independent of the data file (the data file is
        replaced atomically by ``_atomic_write`` and a lock on the replaced fd
        would be lost).

        The sequence callers MUST follow inside this context manager:
          1. Read the current file contents (re-read, not a cached snapshot).
          2. Merge the new record into the fresh contents.
          3. Write atomically via ``write_file`` / ``_atomic_write``.

        Skipping step 1 inside this lock leaves the stale-snapshot race intact.

        LIMITATION: ``flock`` is unreliable on NFS and other network filesystems.
        For multi-host deployments use PostgreSQL storage instead.

        On non-POSIX platforms (Windows) where ``fcntl`` is unavailable this
        context manager is a no-op; concurrent CLI invocations will race.
        """
        if not _FCNTL_AVAILABLE:
            yield
            return

        assert _fcntl is not None  # noqa: S101  # narrowed: _FCNTL_AVAILABLE implies _fcntl was imported

        lock_path = self.file_path.parent / f".{self.file_path.name}.lock"
        # Open (or create) the lock file; O_CREAT handles the not-yet-exists case.
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
            try:
                yield
            finally:
                _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    def read_file(self) -> str:
        """
        Read file content safely.

        Returns:
            File content as string, empty string if file doesn't exist
        """
        try:
            if not self.file_path.exists():
                self.logger.debug("File does not exist: %s", self.file_path)
                return ""

            with open(self.file_path, encoding="utf-8") as f:
                content = f.read()

            self.logger.debug("Read %s characters from %s", len(content), self.file_path)
            return content

        except Exception as e:
            self.logger.error("Failed to read file %s: %s", self.file_path, e)
            raise

    def write_file(self, content: str) -> None:
        """
        Write content to file atomically.

        Args:
            content: Content to write
        """
        try:
            self._atomic_write(content)
            self.logger.debug("Wrote %s characters to %s", len(content), self.file_path)
        except Exception as e:
            self.logger.error("Failed to write file %s: %s", self.file_path, e)
            raise

    def _atomic_write(self, content: str) -> None:
        """
        Perform atomic write operation using temporary file.

        Args:
            content: Content to write
        """
        # Create temporary file in same directory
        temp_dir = self.file_path.parent
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=temp_dir,
            delete=False,
            prefix=f".{self.file_path.name}.tmp",
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())  # Force write to disk

        try:
            # Atomic move (rename) on most filesystems
            if os.name == "nt" and self.file_path.exists():  # Windows
                self.file_path.unlink()
            temp_path.replace(self.file_path)

        except Exception:
            # Clean up temp file on failure
            if temp_path.exists():
                temp_path.unlink()
            raise

    def create_backup(self) -> Optional[str]:
        """
        Create backup of current file.

        Returns:
            Path to backup file, None if no file to backup
        """
        if not self.file_path.exists():
            return None

        if not self.backup_enabled:
            return None

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = (
                self.backup_dir / f"{self.file_path.stem}.backup_{timestamp}{self.file_path.suffix}"
            )

            # Skip if content unchanged since last backup
            _existing = sorted(
                self.backup_dir.glob(f"{self.file_path.stem}.backup_*{self.file_path.suffix}"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if _existing:
                try:
                    _cur = self.calculate_checksum(self.file_path.read_text(encoding="utf-8"))
                    _last = self.calculate_checksum(_existing[0].read_text(encoding="utf-8"))
                    if _cur == _last:
                        self.logger.debug("Skipping backup — content unchanged")
                        return None
                except Exception as e:
                    self.logger.debug("Failed to compare checksums for backup optimisation: %s", e)

            shutil.copy2(self.file_path, backup_path)
            self.logger.debug("Created backup: %s", backup_path)

            # Clean up old backups
            self._cleanup_old_backups()

            return str(backup_path)

        except Exception as e:
            self.logger.error("Failed to create backup: %s", e)
            return None

    def _cleanup_old_backups(self) -> None:
        """Clean up old backup files, keeping only the most recent ones."""
        try:
            # Find all backup files
            backup_pattern = f"{self.file_path.stem}.backup_*{self.file_path.suffix}"
            backup_files = list(self.backup_dir.glob(backup_pattern))

            # Sort by modification time (newest first)
            backup_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

            # Remove old backups
            for backup_file in backup_files[self.backup_count :]:
                try:
                    backup_file.unlink()
                    self.logger.debug("Removed old backup: %s", backup_file)
                except Exception as e:
                    self.logger.warning("Failed to remove old backup %s: %s", backup_file, e)

        except Exception as e:
            self.logger.error("Failed to cleanup old backups: %s", e)

    def calculate_checksum(self, content: str) -> str:
        """
        Calculate SHA-256 checksum of content.

        Args:
            content: Content to checksum

        Returns:
            Hexadecimal checksum string
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def verify_file_integrity(self, expected_checksum: Optional[str] = None) -> bool:
        """
        Verify file integrity using checksum.

        Args:
            expected_checksum: Expected checksum, if None just checks if file is readable

        Returns:
            True if file is valid, False otherwise
        """
        try:
            if not self.file_path.exists():
                return False

            content = self.read_file()

            if expected_checksum is None:
                # Just check if file is readable and not empty
                return len(content.strip()) > 0

            actual_checksum = self.calculate_checksum(content)
            is_valid = actual_checksum == expected_checksum

            if not is_valid:
                self.logger.warning(
                    "File integrity check failed. Expected: %s, Actual: %s",
                    expected_checksum,
                    actual_checksum,
                )

            return is_valid

        except Exception as e:
            self.logger.error("File integrity verification failed: %s", e)
            return False

    def recover_from_backup(self) -> bool:
        """
        Recover file from most recent backup.

        Returns:
            True if recovery successful, False otherwise
        """
        try:
            # Find most recent backup
            backup_pattern = f"{self.file_path.stem}.backup_*{self.file_path.suffix}"
            backup_files = list(self.backup_dir.glob(backup_pattern))

            if not backup_files:
                self.logger.warning("No backup files found for recovery")
                return False

            # Sort by modification time (newest first)
            backup_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            latest_backup = backup_files[0]

            # Copy backup to main file
            shutil.copy2(latest_backup, self.file_path)
            self.logger.info("Recovered file from backup: %s", latest_backup)

            return True

        except Exception as e:
            self.logger.error("Failed to recover from backup: %s", e)
            return False

    def file_exists(self) -> bool:
        """Check if file exists."""
        return self.file_path.exists()

    def get_file_size(self) -> int:
        """Get file size in bytes."""
        if not self.file_path.exists():
            return 0
        return self.file_path.stat().st_size

    def get_modification_time(self) -> Optional[datetime]:
        """Get file modification time."""
        if not self.file_path.exists():
            return None
        return datetime.fromtimestamp(self.file_path.stat().st_mtime)
