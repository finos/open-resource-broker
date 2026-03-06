"""Tests for VersionManager component."""

import pytest

from orb.infrastructure.storage.components.version_manager import (
    MemoryVersionManager,
    NoOpVersionManager,
    VersionManager,
)


class TestVersionManagerInterface:
    """Test VersionManager interface."""

    def test_version_manager_interface_is_abstract(self):
        """Test that VersionManager is abstract."""
        with pytest.raises(TypeError):
            VersionManager()


class TestMemoryVersionManager:
    """Test MemoryVersionManager implementation."""

    def test_initialization(self):
        """Test version manager initialization."""
        manager = MemoryVersionManager()
        assert manager.logger is not None
        assert manager._versions == {}

    def test_get_version_nonexistent(self):
        """Test getting version for non-existent entity."""
        manager = MemoryVersionManager()

        result = manager.get_version("nonexistent")

        assert result is None

    def test_increment_version_new_entity(self):
        """Test incrementing version for new entity."""
        manager = MemoryVersionManager()

        result = manager.increment_version("entity-1")

        assert result == 1
        assert manager.get_version("entity-1") == 1

    def test_increment_version_existing_entity(self):
        """Test incrementing version for existing entity."""
        manager = MemoryVersionManager()

        manager.increment_version("entity-1")
        result = manager.increment_version("entity-1")

        assert result == 2
        assert manager.get_version("entity-1") == 2

    def test_set_version(self):
        """Test setting specific version."""
        manager = MemoryVersionManager()

        manager.set_version("entity-1", 5)

        assert manager.get_version("entity-1") == 5

    def test_set_version_overwrites(self):
        """Test that set_version overwrites existing version."""
        manager = MemoryVersionManager()

        manager.increment_version("entity-1")  # Version 1
        manager.set_version("entity-1", 10)

        assert manager.get_version("entity-1") == 10

    def test_multiple_entities(self):
        """Test version management for multiple entities."""
        manager = MemoryVersionManager()

        manager.increment_version("entity-1")  # Version 1
        manager.increment_version("entity-2")  # Version 1
        manager.increment_version("entity-1")  # Version 2

        assert manager.get_version("entity-1") == 2
        assert manager.get_version("entity-2") == 1


class TestNoOpVersionManager:
    """Test NoOpVersionManager implementation."""

    def test_get_version_always_returns_none(self):
        """Test that get_version always returns None."""
        manager = NoOpVersionManager()

        result = manager.get_version("any-entity")

        assert result is None

    def test_increment_version_always_returns_one(self):
        """Test that increment_version always returns 1."""
        manager = NoOpVersionManager()

        result1 = manager.increment_version("entity-1")
        result2 = manager.increment_version("entity-1")

        assert result1 == 1
        assert result2 == 1

    def test_set_version_does_nothing(self):
        """Test that set_version does nothing."""
        manager = NoOpVersionManager()

        # Should not raise exception
        manager.set_version("entity-1", 5)

        # Should still return None
        assert manager.get_version("entity-1") is None
