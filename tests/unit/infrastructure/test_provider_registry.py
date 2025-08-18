"""Unit tests for Provider Registry."""

from unittest.mock import Mock

import pytest

from domain.base.exceptions import ConfigurationError
from infrastructure.registry.provider_registry import (
    ProviderRegistry,
    UnsupportedProviderError,
    get_provider_registry,
)


@pytest.mark.unit
class TestProviderRegistry:
    """Test cases for Provider Registry."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create a fresh registry for each test
        self.registry = ProviderRegistry()

        # Mock factories
        self.mock_strategy_factory = Mock(return_value="mock_strategy")
        self.mock_config_factory = Mock(return_value="mock_config")
        self.mock_resolver_factory = Mock(return_value="mock_resolver")
        self.mock_validator_factory = Mock(return_value="mock_validator")

    def test_register_provider(self):
        """Test provider registration."""
        self.registry.register_provider(
            provider_type="test_provider",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
        )

        assert self.registry.is_provider_registered("test_provider")
        assert "test_provider" in self.registry.get_registered_providers()

    def test_register_provider_with_optional_factories(self):
        """Test provider registration with optional factories."""
        self.registry.register_provider(
            provider_type="test_provider",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
            resolver_factory=self.mock_resolver_factory,
            validator_factory=self.mock_validator_factory,
        )

        assert self.registry.is_provider_registered("test_provider")

    def test_register_duplicate_provider_raises_error(self):
        """Test that registering duplicate provider raises error."""
        self.registry.register_provider(
            provider_type="test_provider",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
        )

        with pytest.raises(ValueError, match="already registered"):
            self.registry.register_provider(
                provider_type="test_provider",
                strategy_factory=self.mock_strategy_factory,
                config_factory=self.mock_config_factory,
            )

    def test_unregister_provider(self):
        """Test provider unregistration."""
        self.registry.register_provider(
            provider_type="test_provider",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
        )

        assert self.registry.unregister_provider("test_provider")
        assert not self.registry.is_provider_registered("test_provider")
        assert "test_provider" not in self.registry.get_registered_providers()

    def test_unregister_nonexistent_provider(self):
        """Test unregistering non-existent provider."""
        assert not self.registry.unregister_provider("nonexistent_provider")

    def test_create_strategy(self):
        """Test strategy creation."""
        self.registry.register_provider(
            provider_type="test_provider",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
        )

        config = {"test": "config"}
        strategy = self.registry.create_strategy("test_provider", config)

        assert strategy == "mock_strategy"
        self.mock_strategy_factory.assert_called_once_with(config)

    def test_create_strategy_unsupported_provider(self):
        """Test strategy creation with unsupported provider."""
        with pytest.raises(UnsupportedProviderError, match="not registered"):
            self.registry.create_strategy("unsupported_provider", {})

    def test_create_strategy_factory_error(self):
        """Test strategy creation when factory raises error."""
        failing_factory = Mock(side_effect=Exception("Factory error"))

        self.registry.register_provider(
            provider_type="test_provider",
            strategy_factory=failing_factory,
            config_factory=self.mock_config_factory,
        )

        with pytest.raises(ConfigurationError, match="Failed to create strategy"):
            self.registry.create_strategy("test_provider", {})

    def test_create_config(self):
        """Test configuration creation."""
        self.registry.register_provider(
            provider_type="test_provider",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
        )

        data = {"test": "data"}
        config = self.registry.create_config("test_provider", data)

        assert config == "mock_config"
        self.mock_config_factory.assert_called_once_with(data)

    def test_create_config_unsupported_provider(self):
        """Test config creation with unsupported provider."""
        with pytest.raises(UnsupportedProviderError, match="not registered"):
            self.registry.create_config("unsupported_provider", {})

    def test_create_resolver(self):
        """Test resolver creation."""
        self.registry.register_provider(
            provider_type="test_provider",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
            resolver_factory=self.mock_resolver_factory,
        )

        resolver = self.registry.create_resolver("test_provider")

        assert resolver == "mock_resolver"
        self.mock_resolver_factory.assert_called_once()

    def test_create_resolver_no_factory(self):
        """Test resolver creation when no factory is registered."""
        self.registry.register_provider(
            provider_type="test_provider",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
        )

        resolver = self.registry.create_resolver("test_provider")
        assert resolver is None

    def test_create_resolver_unregistered_provider(self):
        """Test resolver creation for unregistered provider."""
        resolver = self.registry.create_resolver("unregistered_provider")
        assert resolver is None

    def test_create_validator(self):
        """Test validator creation."""
        self.registry.register_provider(
            provider_type="test_provider",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
            validator_factory=self.mock_validator_factory,
        )

        validator = self.registry.create_validator("test_provider")

        assert validator == "mock_validator"
        self.mock_validator_factory.assert_called_once()

    def test_create_validator_no_factory(self):
        """Test validator creation when no factory is registered."""
        self.registry.register_provider(
            provider_type="test_provider",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
        )

        validator = self.registry.create_validator("test_provider")
        assert validator is None

    def test_clear_registrations(self):
        """Test clearing all registrations."""
        self.registry.register_provider(
            provider_type="test_provider1",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
        )
        self.registry.register_provider(
            provider_type="test_provider2",
            strategy_factory=self.mock_strategy_factory,
            config_factory=self.mock_config_factory,
        )

        assert len(self.registry.get_registered_providers()) == 2

        self.registry.clear_registrations()

        assert len(self.registry.get_registered_providers()) == 0

    def test_singleton_behavior(self):
        """Test that get_provider_registry returns singleton."""
        registry1 = get_provider_registry()
        registry2 = get_provider_registry()

        assert registry1 is registry2

    def test_thread_safety(self):
        """Test thread-safe registration."""
        import threading

        results = []
        errors = []

        def register_provider(provider_id):
            try:
                self.registry.register_provider(
                    provider_type=f"provider_{provider_id}",
                    strategy_factory=lambda x: f"strategy_{provider_id}",
                    config_factory=lambda x: f"config_{provider_id}",
                )
                results.append(provider_id)
            except Exception as e:
                errors.append(e)

        # Create multiple threads registering providers
        threads = []
        for i in range(10):
            thread = threading.Thread(target=register_provider, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Check results
        assert len(errors) == 0
        assert len(results) == 10
        assert len(self.registry.get_registered_providers()) == 10
