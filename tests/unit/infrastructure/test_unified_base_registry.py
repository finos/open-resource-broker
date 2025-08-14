"""Tests for integrated base registry supporting both single and multi choice patterns."""

from unittest.mock import Mock

import pytest

from src.infrastructure.registry.base_registry import RegistryMode
from src.infrastructure.registry.provider_registry import (
    ProviderRegistry,
    get_provider_registry,
)
from src.infrastructure.registry.scheduler_registry import (
    SchedulerRegistry,
    get_scheduler_registry,
)
from src.infrastructure.registry.storage_registry import (
    StorageRegistry,
    get_storage_registry,
)


class TestIntegratedBaseRegistry:
    """Test integrated base registry functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_strategy_factory = Mock(return_value="strategy_instance")
        self.mock_config_factory = Mock(return_value="config_instance")
        self.mock_additional_factory = Mock(return_value="additional_instance")

        # Clear registrations from singleton instances to ensure clean state
        StorageRegistry().clear_registrations()
        SchedulerRegistry().clear_registrations()
        ProviderRegistry().clear_registrations()

    def test_storage_registry_single_choice_mode(self):
        """Test StorageRegistry uses single choice mode."""
        registry = get_storage_registry()

        assert registry.mode == RegistryMode.SINGLE_CHOICE

        # Register storage type
        registry.register("json", self.mock_strategy_factory, self.mock_config_factory)

        # Verify registration
        assert registry.is_registered("json")
        assert "json" in registry.get_registered_types()

        # Create strategy
        strategy = registry.create_strategy("json", {"test": "config"})
        assert strategy == "strategy_instance"

        # Verify instance registration is prevented
        with pytest.raises(
            ValueError,
            match="Instance registration only supported in MULTI_CHOICE mode",
        ):
            registry.register_instance(
                "json",
                "json-primary",
                self.mock_strategy_factory,
                self.mock_config_factory,
            )

    def test_scheduler_registry_single_choice_mode(self):
        """Test SchedulerRegistry uses single choice mode."""
        registry = get_scheduler_registry()

        assert registry.mode == RegistryMode.SINGLE_CHOICE

        # Register scheduler type
        registry.register("hostfactory", self.mock_strategy_factory,
                          self.mock_config_factory)

        # Verify registration
        assert registry.is_registered("hostfactory")
        assert "hostfactory" in registry.get_registered_types()

        # Create strategy
        strategy = registry.create_strategy("hostfactory", {"test": "config"})
        assert strategy == "strategy_instance"

    def test_provider_registry_multi_choice_mode(self):
        """Test ProviderRegistry uses multi choice mode."""
        registry = get_provider_registry()

        assert registry.mode == RegistryMode.MULTI_CHOICE

        # Register provider type
        registry.register("aws", self.mock_strategy_factory, self.mock_config_factory)

        # Verify type registration
        assert registry.is_registered("aws")
        assert "aws" in registry.get_registered_types()

        # Register provider instances
        registry.register_provider_instance(
            "aws", "aws-primary", self.mock_strategy_factory, self.mock_config_factory
        )
        registry.register_provider_instance(
            "aws", "aws-secondary", self.mock_strategy_factory, self.mock_config_factory
        )

        # Verify instance registration
        assert registry.is_instance_registered("aws-primary")
        assert registry.is_instance_registered("aws-secondary")
        assert "aws-primary" in registry.get_registered_instances()
        assert "aws-secondary" in registry.get_registered_instances()

        # Create strategies by type and instance
        type_strategy = registry.create_strategy("aws", {"config": "type"})
        instance_strategy = registry.create_strategy_from_instance(
            "aws-primary", {"config": "instance"}
        )

        assert type_strategy == "strategy_instance"
        assert instance_strategy == "strategy_instance"

    def test_storage_registry_backward_compatibility(self):
        """Test StorageRegistry backward compatibility methods."""
        registry = get_storage_registry()

        # Test backward compatibility methods
        registry.register_storage(
            "json", self.mock_strategy_factory, self.mock_config_factory)

        assert registry.is_storage_registered("json")
        assert "json" in registry.get_registered_storage_types()

        # Test config creation
        config = registry.create_config("json", {"test": "data"})
        assert config == "config_instance"

        # Test unit of work creation (should return None for no factory)
        uow = registry.create_unit_of_work("json")
        assert uow is None

    def test_provider_registry_backward_compatibility(self):
        """Test ProviderRegistry backward compatibility methods."""
        registry = get_provider_registry()

        # Test backward compatibility methods
        registry.register_provider(
            "aws", self.mock_strategy_factory, self.mock_config_factory)

        assert registry.is_provider_registered("aws")
        assert "aws" in registry.get_registered_providers()

        # Test config creation
        config = registry.create_config("aws", {"test": "data"})
        assert config == "config_instance"

        # Test resolver/validator creation (should return None for no factory)
        resolver = registry.create_resolver("aws")
        validator = registry.create_validator("aws")
        assert resolver is None
        assert validator is None

    def test_additional_factories_support(self):
        """Test support for additional factories."""
        registry = get_provider_registry()

        # Register with additional factories
        registry.register(
            "aws",
            self.mock_strategy_factory,
            self.mock_config_factory,
            resolver_factory=self.mock_additional_factory,
            validator_factory=self.mock_additional_factory,
        )

        # Create additional components
        resolver = registry.create_resolver("aws")
        validator = registry.create_validator("aws")

        assert resolver == "additional_instance"
        assert validator == "additional_instance"

    def test_error_handling(self):
        """Test error handling for unregistered types and instances."""
        storage_registry = get_storage_registry()
        provider_registry = get_provider_registry()

        # Test storage registry errors
        with pytest.raises(Exception):  # UnsupportedStorageError
            storage_registry.create_strategy("unknown", {})

        # Test provider registry errors
        with pytest.raises(Exception):  # UnsupportedProviderError
            provider_registry.create_strategy("unknown", {})

        with pytest.raises(Exception):  # UnsupportedProviderError
            provider_registry.create_strategy_from_instance("unknown-instance", {})

    def test_singleton_behavior(self):
        """Test singleton behavior per registry class."""
        storage1 = get_storage_registry()
        storage2 = get_storage_registry()
        assert storage1 is storage2

        scheduler1 = get_scheduler_registry()
        scheduler2 = get_scheduler_registry()
        assert scheduler1 is scheduler2

        provider1 = get_provider_registry()
        provider2 = get_provider_registry()
        assert provider1 is provider2

        # Different registry classes should be different instances
        assert storage1 is not scheduler1
        assert storage1 is not provider1
        assert scheduler1 is not provider1

    def test_clear_registrations(self):
        """Test clearing registrations."""
        registry = get_provider_registry()

        # Register types and instances
        registry.register("aws", self.mock_strategy_factory, self.mock_config_factory)
        registry.register_provider_instance(
            "aws", "aws-primary", self.mock_strategy_factory, self.mock_config_factory
        )

        assert len(registry.get_registered_types()) > 0
        assert len(registry.get_registered_instances()) > 0

        # Clear all
        registry.clear_registrations()

        assert len(registry.get_registered_types()) == 0
        assert len(registry.get_registered_instances()) == 0
