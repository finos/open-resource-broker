"""Version management components for entity versioning."""

from abc import ABC, abstractmethod
from typing import Optional

from infrastructure.logging.logger import get_logger


class VersionManager(ABC):
    """Base interface for entity version management."""

    @abstractmethod
    def get_version(self, entity_id: str) -> Optional[int]:
        """Get current version for entity."""

    @abstractmethod
    def increment_version(self, entity_id: str) -> int:
        """Increment version and return new version."""

    @abstractmethod
    def set_version(self, entity_id: str, version: int) -> None:
        """Set specific version for entity."""


class MemoryVersionManager(VersionManager):
    """In-memory version manager implementation."""

    def __init__(self) -> None:
        """Initialize version manager."""
        self._versions: dict[str, int] = {}
        self.logger = get_logger(__name__)

    def get_version(self, entity_id: str) -> Optional[int]:
        """Get current version for entity."""
        return self._versions.get(entity_id)

    def increment_version(self, entity_id: str) -> int:
        """Increment version and return new version."""
        current = self._versions.get(entity_id, 0)
        new_version = current + 1
        self._versions[entity_id] = new_version
        return new_version

    def set_version(self, entity_id: str, version: int) -> None:
        """Set specific version for entity."""
        self._versions[entity_id] = version


class NoOpVersionManager(VersionManager):
    """No-operation version manager that doesn't track versions."""

    def get_version(self, entity_id: str) -> Optional[int]:
        """Always return None (no versioning)."""
        return None

    def increment_version(self, entity_id: str) -> int:
        """Always return 1 (no versioning)."""
        return 1

    def set_version(self, entity_id: str, version: int) -> None:
        """Do nothing (no versioning)."""
        pass
