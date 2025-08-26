"""Unit tests for Storage Registry (Corrected Architecture)."""

import threading
from unittest.mock import Mock

import pytest

from domain.base.exceptions import ConfigurationError
from infrastructure.registry.storage_registry import (
    StorageRegistration,
    StorageRegistry,
    UnsupportedStorageError,
    get_storage_registry,
    reset_storage_registry,
)


class TestStorageRegistration:
    """Test StorageRegistration class."""

    def test_storage_registration_creation(self):
        """Test creating storage registration."""
        strategy_factory = Mock()
        config_factory = Mock()

        registration = StorageRegistration(
            storage_type="test_storage",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        assert registration.storage_type == "test_storage"
        assert registration.strategy_factory == strategy_factory
        assert registration.config_factory == config_factory

    def test_storage_registration_repr(self):
        """Test storage registration string representation."""
        registration = StorageRegistration(
            storage_type="test_storage", strategy_factory=Mock(), config_factory=Mock()
        )

        assert repr(registration) == "StorageRegistration(type='test_storage')"


class TestStorageRegistry:
    """Test StorageRegistry class."""

    def setup_method(self):
        """Set up test fixtures."""
        reset_storage_registry()
        self.registry = StorageRegistry()

    def teardown_method(self):
        """Clean up after tests."""
        reset_storage_registry()

    def test_singleton_pattern(self):
        """Test that StorageRegistry is a singleton."""
        registry1 = StorageRegistry()
        registry2 = StorageRegistry()

        assert registry1 is registry2
        assert id(registry1) == id(registry2)

    def test_global_registry_function(self):
        """Test get_storage_registry function."""
        registry1 = get_storage_registry()
        registry2 = get_storage_registry()

        assert registry1 is registry2
        assert isinstance(registry1, StorageRegistry)

    def test_register_storage_success(self):
        """Test successful storage registration."""
        strategy_factory = Mock()
        config_factory = Mock()

        self.registry.register_storage(
            storage_type="test_storage",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        assert self.registry.is_storage_registered("test_storage")
        assert "test_storage" in self.registry.get_registered_storage_types()

    def test_register_storage_duplicate_error(self):
        """Test error when registering duplicate storage type."""
        strategy_factory = Mock()
        config_factory = Mock()

        # Register first time
        self.registry.register_storage(
            storage_type="test_storage",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        # Try to register again
        with pytest.raises(ConfigurationError, match="already registered"):
            self.registry.register_storage(
                storage_type="test_storage",
                strategy_factory=strategy_factory,
                config_factory=config_factory,
            )

    def test_create_strategy_success(self):
        """Test successful strategy creation."""
        mock_strategy = Mock()
        strategy_factory = Mock(return_value=mock_strategy)
        config_factory = Mock()
        config = {"key": "value"}

        self.registry.register_storage(
            storage_type="test_storage",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        result = self.registry.create_strategy("test_storage", config)

        assert result == mock_strategy
        strategy_factory.assert_called_once_with(config)

    def test_create_strategy_unregistered_error(self):
        """Test error when creating strategy for unregistered storage type."""
        with pytest.raises(UnsupportedStorageError, match="not registered"):
            self.registry.create_strategy("unregistered_storage", {})

    def test_create_strategy_factory_error(self):
        """Test error handling when strategy factory fails."""
        strategy_factory = Mock(side_effect=Exception("Factory error"))
        config_factory = Mock()

        self.registry.register_storage(
            storage_type="test_storage",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        with pytest.raises(ConfigurationError, match="Failed to create storage strategy"):
            self.registry.create_strategy("test_storage", {})

    def test_create_config_success(self):
        """Test successful config creation."""
        mock_config = Mock()
        strategy_factory = Mock()
        config_factory = Mock(return_value=mock_config)
        data = {"key": "value"}

        self.registry.register_storage(
            storage_type="test_storage",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        result = self.registry.create_config("test_storage", data)

        assert result == mock_config
        config_factory.assert_called_once_with(data)

    def test_create_config_unregistered_error(self):
        """Test error when creating config for unregistered storage type."""
        with pytest.raises(UnsupportedStorageError, match="not registered"):
            self.registry.create_config("unregistered_storage", {})

    def test_get_registered_storage_types(self):
        """Test getting list of registered storage types."""
        assert self.registry.get_registered_storage_types() == []

        self.registry.register_storage("storage1", Mock(), Mock())
        self.registry.register_storage("storage2", Mock(), Mock())

        types = self.registry.get_registered_storage_types()
        assert set(types) == {"storage1", "storage2"}

    def test_is_storage_registered(self):
        """Test checking if storage type is registered."""
        assert not self.registry.is_storage_registered("test_storage")

        self.registry.register_storage("test_storage", Mock(), Mock())

        assert self.registry.is_storage_registered("test_storage")
        assert not self.registry.is_storage_registered("other_storage")

    def test_clear_registrations(self):
        """Test clearing all registrations."""
        self.registry.register_storage("storage1", Mock(), Mock())
        self.registry.register_storage("storage2", Mock(), Mock())

        assert len(self.registry.get_registered_storage_types()) == 2

        self.registry.clear_registrations()

        assert len(self.registry.get_registered_storage_types()) == 0

    def test_thread_safety(self):
        """Test thread safety of storage registry."""
        results = []
        errors = []

        def register_storage(storage_id):
            try:
                self.registry.register_storage(
                    storage_type=f"storage_{storage_id}",
                    strategy_factory=Mock(),
                    config_factory=Mock(),
                )
                results.append(storage_id)
            except Exception as e:
                errors.append(e)

        # Create multiple threads that register storage types simultaneously
        threads = []
        for i in range(10):
            thread = threading.Thread(target=register_storage, args=(i,))
            threads.append(thread)

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Unexpected errors: {errors}"
        assert len(results) == 10
        assert len(self.registry.get_registered_storage_types()) == 10

    def test_concurrent_access(self):
        """Test concurrent access to registry operations."""
        # Register a storage type
        mock_strategy = Mock()
        strategy_factory = Mock(return_value=mock_strategy)

        self.registry.register_storage(
            storage_type="concurrent_storage",
            strategy_factory=strategy_factory,
            config_factory=Mock(),
        )

        results = []
        errors = []

        def create_strategy():
            try:
                strategy = self.registry.create_strategy("concurrent_storage", {})
                results.append(strategy)
            except Exception as e:
                errors.append(e)

        # Create multiple threads that create strategies simultaneously
        threads = []
        for _i in range(20):
            thread = threading.Thread(target=create_strategy)
            threads.append(thread)

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Unexpected errors: {errors}"
        assert len(results) == 20
        assert all(result == mock_strategy for result in results)


class TestStorageRegistryIntegration:
    """Integration tests for storage registry."""

    def setup_method(self):
        """Set up test fixtures."""
        reset_storage_registry()

    def teardown_method(self):
        """Clean up after tests."""
        reset_storage_registry()

    def test_full_storage_lifecycle(self):
        """Test complete storage registration and usage lifecycle."""
        registry = get_storage_registry()

        # Mock factories
        mock_strategy = Mock()
        mock_config = Mock()

        strategy_factory = Mock(return_value=mock_strategy)
        config_factory = Mock(return_value=mock_config)

        # Register storage type
        registry.register_storage(
            storage_type="integration_storage",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
        )

        # Test all operations
        config_data = {"key": "value"}

        # Create config
        config = registry.create_config("integration_storage", config_data)
        assert config == mock_config
        config_factory.assert_called_once_with(config_data)

        # Create strategy
        strategy = registry.create_strategy("integration_storage", config)
        assert strategy == mock_strategy
        strategy_factory.assert_called_once_with(config)

        # Verify registry state
        assert registry.is_storage_registered("integration_storage")
        assert "integration_storage" in registry.get_registered_storage_types()

    def test_reset_registry_function(self):
        """Test reset_storage_registry function."""
        registry1 = get_storage_registry()
        registry1.register_storage("test_storage", Mock(), Mock())

        assert registry1.is_storage_registered("test_storage")

        reset_storage_registry()

        registry2 = get_storage_registry()
        assert registry2 is not registry1
        assert not registry2.is_storage_registered("test_storage")
