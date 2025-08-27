"""File management components for file-based storage operations."""

import hashlib
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from infrastructure.logging.logger import get_logger


class FileManager:
    """
    File operations manager for atomic file operations, backups, and integrity checking.

    Handles all file I/O operations with safety features like atomic writes,
    backup management, and integrity verification.
    """

    def __init__(self, file_path: str, create_dirs: bool = True, backup_count: int = 5) -> None:
        """
        Initialize file manager.

        Args:
            file_path: Path to the main data file
            create_dirs: Whether to create parent directories
            backup_count: Number of backup files to keep
        """
        self.file_path = Path(file_path)
        self.backup_count = backup_count
        self.logger = get_logger(__name__)

        # Create parent directories if needed
        if create_dirs and not self.file_path.parent.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self.logger.info("Created directory: %s", self.file_path.parent)

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

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.file_path.with_suffix(f".backup_{timestamp}{self.file_path.suffix}")

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
            backup_files = list(self.file_path.parent.glob(backup_pattern))

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
            backup_files = list(self.file_path.parent.glob(backup_pattern))

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
