"""Tests for RegistryFactory."""

import pytest

from orb.infrastructure.registry.registry_factory import RegistryFactory


class MockService:
    """Mock service for testing."""

    def __init__(self, config=None):
        self.config = config


class TestRegistryFactory:
    """Test RegistryFactory functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.factory = RegistryFactory()

    def test_register_and_create_instance(self):
        """Test registering constructor and creating instance."""
        self.factory.register_constructor("mock", MockService, {"config": "test"})

        instance = self.factory.create_instance("mock")

        assert isinstance(instance, MockService)
        assert instance.config == "test"

    def test_create_instance_with_override_kwargs(self):
        """Test creating instance with override kwargs."""
        self.factory.register_constructor("mock", MockService, {"config": "default"})

        instance = self.factory.create_instance("mock", config="override")

        assert isinstance(instance, MockService)
        assert instance.config == "override"

    def test_create_instance_unregistered_raises_error(self):
        """Test that creating unregistered instance raises error."""
        with pytest.raises(ValueError, match="No constructor registered for unknown"):
            self.factory.create_instance("unknown")

    def test_register_constructor_without_dependencies(self):
        """Test registering constructor without dependencies."""
        self.factory.register_constructor("mock", MockService)

        instance = self.factory.create_instance("mock")

        assert isinstance(instance, MockService)
        assert instance.config is None
