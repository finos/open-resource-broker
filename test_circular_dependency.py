#!/usr/bin/env python3
"""Test that BaseRegistry can be created without circular dependency."""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def test_registry_creation():
    """Test that registry can be created without triggering get_container()."""
    print("Testing BaseRegistry creation without circular dependency...")

    # Import should not trigger get_container()
    from infrastructure.registry.base_registry import BaseRegistry, RegistryMode

    # Create a concrete test registry
    class TestRegistry(BaseRegistry):
        def register(self, type_name, strategy_factory, config_factory, **kwargs):
            self.register_type(type_name, strategy_factory, config_factory, **kwargs)

        def create_strategy(self, type_name, config):
            return self.create_strategy_by_type(type_name, config)

    # This should not trigger get_container()
    registry = TestRegistry(RegistryMode.SINGLE_CHOICE)
    print("✓ Registry created successfully")

    # Accessing logger should trigger lazy loading
    _ = registry.logger  # Access logger to trigger lazy loading
    print("✓ Logger accessed successfully")

    print("✓ No circular dependency detected")


if __name__ == "__main__":
    test_registry_creation()
