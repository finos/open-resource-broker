"""Tests for BaseRegistry consolidated methods."""

from unittest.mock import Mock

from infrastructure.registry.base_registry import BaseRegistry, RegistryMode


class MockStrategy:
    """Mock strategy for testing."""

    def __init__(self, config=None):
        self.config = config


class ConcreteRegistry(BaseRegistry):
    """Concrete registry for testing."""

    def register(self, type_name: str, strategy_factory, config_factory, **kwargs):
        """Register a strategy factory."""
        self.register_type(type_name, strategy_factory, config_factory, **kwargs)

    def create_strategy(self, type_name: str, config):
        """Create a strategy instance."""
        return self.create_strategy_by_type(type_name, config)


class TestBaseRegistryConsolidatedMethods:
    """Test consolidated methods in BaseRegistry."""

    def setup_method(self):
        """Set up test fixtures."""
        # Clear singleton instances to avoid interference
        ConcreteRegistry._instances.clear()
        self.registry = ConcreteRegistry(mode=RegistryMode.SINGLE_CHOICE)

    def test_is_registered_consolidated(self):
        """Test consolidated is_registered method."""
        strategy_factory = Mock(return_value=MockStrategy())
        config_factory = Mock()

        # Not registered initially
        assert not self.registry.is_registered("test_type")

        # Register and verify
        self.registry.register_type("test_type", strategy_factory, config_factory)
        assert self.registry.is_registered("test_type")

    def test_get_registered_types_consolidated(self):
        """Test consolidated get_registered_types method."""
        strategy_factory = Mock(return_value=MockStrategy())
        config_factory = Mock()

        # Empty initially
        assert self.registry.get_registered_types() == []

        # Register types and verify
        self.registry.register_type("type1", strategy_factory, config_factory)
        self.registry.register_type("type2", strategy_factory, config_factory)

        registered_types = self.registry.get_registered_types()
        assert "type1" in registered_types
        assert "type2" in registered_types
        assert len(registered_types) == 2

    def test_format_not_registered_error_consolidated(self):
        """Test consolidated error formatting method."""
        strategy_factory = Mock(return_value=MockStrategy())
        config_factory = Mock()

        # Test with no registrations
        error_msg = self.registry.format_not_registered_error("missing", "provider")
        assert "No providers registered" in error_msg

        # Test with type registrations
        self.registry.register_type("aws", strategy_factory, config_factory)
        error_msg = self.registry.format_not_registered_error("missing", "provider")
        assert "Provider 'missing' not found" in error_msg
        assert "Available provider types: aws" in error_msg

    def test_format_registry_error_backward_compatibility(self):
        """Test backward compatibility alias for error formatting."""
        strategy_factory = Mock(return_value=MockStrategy())
        config_factory = Mock()

        self.registry.register_type("aws", strategy_factory, config_factory)

        # Both methods should return the same result
        new_error = self.registry.format_not_registered_error("missing", "provider")
        old_error = self.registry.format_registry_error("missing", "provider")

        assert new_error == old_error

    def test_multi_choice_instance_methods(self):
        """Test instance-related methods in multi-choice mode."""
        # Clear and create multi-choice registry
        ConcreteRegistry._instances.clear()
        registry = ConcreteRegistry(mode=RegistryMode.MULTI_CHOICE)

        strategy_factory = Mock(return_value=MockStrategy())
        config_factory = Mock()

        # Test instance registration and checking
        assert not registry.is_instance_registered("aws-us-east-1")

        registry.register_instance("aws", "aws-us-east-1", strategy_factory, config_factory)
        assert registry.is_instance_registered("aws-us-east-1")

        instances = registry.get_registered_instances()
        assert "aws-us-east-1" in instances

    def test_error_formatting_with_instances(self):
        """Test error formatting includes both types and instances."""
        # Clear and create multi-choice registry
        ConcreteRegistry._instances.clear()
        registry = ConcreteRegistry(mode=RegistryMode.MULTI_CHOICE)

        strategy_factory = Mock(return_value=MockStrategy())
        config_factory = Mock()

        # Register both type and instance
        registry.register_type("aws", strategy_factory, config_factory)
        registry.register_instance("aws", "aws-us-east-1", strategy_factory, config_factory)

        error_msg = registry.format_not_registered_error("missing", "provider")
        assert "Provider 'missing' not found" in error_msg
        assert "Available provider types: aws" in error_msg
        assert "Available provider instances: aws-us-east-1" in error_msg
