"""Tests for BaseRegistry with RegistryFactory integration."""

from unittest.mock import Mock

from orb.infrastructure.registry.base_registry import BaseRegistry, RegistryMode
from orb.infrastructure.registry.registry_factory import RegistryFactory


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


class TestBaseRegistryWithFactory:
    """Test BaseRegistry with RegistryFactory integration."""

    def setup_method(self):
        """Set up test fixtures."""
        # Clear singleton instances to avoid interference
        ConcreteRegistry._instances.clear()

        self.factory = RegistryFactory()
        self.registry = ConcreteRegistry(mode=RegistryMode.SINGLE_CHOICE, factory=self.factory)

    def test_base_registry_constructor_injection(self):
        """Test BaseRegistry accepts factory via constructor."""

        # Create a unique registry class to avoid singleton interference
        class UniqueRegistry(BaseRegistry):
            def register(self, type_name: str, strategy_factory, config_factory, **kwargs):
                self.register_type(type_name, strategy_factory, config_factory, **kwargs)

            def create_strategy(self, type_name: str, config):
                return self.create_strategy_by_type(type_name, config)

        factory = RegistryFactory()
        registry = UniqueRegistry(mode=RegistryMode.SINGLE_CHOICE, factory=factory)

        assert registry._factory is factory

    def test_register_type_uses_factory(self):
        """Test that register_type uses factory for registration."""
        strategy_factory = Mock(return_value=MockStrategy())
        config_factory = Mock()

        self.registry.register_type("test", strategy_factory, config_factory)

        # Should be able to create instance through factory
        instance = self.factory.create_instance("test")
        assert isinstance(instance, MockStrategy)

    def test_create_strategy_by_type_uses_factory(self):
        """Test that create_strategy_by_type uses factory."""
        mock_strategy = MockStrategy(config="test")
        strategy_factory = Mock(return_value=mock_strategy)
        config_factory = Mock()

        self.registry.register_type("test", strategy_factory, config_factory)

        result = self.registry.create_strategy_by_type("test", {"config": "test"})

        assert result is mock_strategy
        strategy_factory.assert_called_once_with({"config": "test"})

    def test_no_ensure_dependencies_method(self):
        """Test that _ensure_dependencies method is removed."""
        assert not hasattr(self.registry, "_ensure_dependencies")

    def test_no_lazy_dependency_properties(self):
        """Test that lazy dependency properties are removed."""
        # These properties should not exist anymore
        assert not hasattr(self.registry, "logger")
        assert not hasattr(self.registry, "config_port")
        assert not hasattr(self.registry, "metrics")
