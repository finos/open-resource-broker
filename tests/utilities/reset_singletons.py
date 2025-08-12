"""Utilities for resetting singletons during testing."""

import importlib
from typing import Any, Type


# Define a fallback registry class
class _FallbackRegistry:
    """Fallback implementation if SingletonRegistry is not available."""

    _instance = None

    @classmethod
    def get_instance(cls):
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def reset(self, singleton_class=None):
        """Reset one or all singleton instances."""


# Import the singleton registry
try:
    from src.infrastructure.patterns.singleton_registry import SingletonRegistry
except ImportError:
    # If the singleton registry doesn't exist yet, use the fallback
    SingletonRegistry = _FallbackRegistry


def _safe_reset_class_instance(module_name: str, class_name: str) -> None:
    """
    Safely reset a class instance.

    Args:
        module_name: The module name
        class_name: The class name
    """
    try:
        module = importlib.import_module(module_name)
        if hasattr(module, class_name):
            cls = getattr(module, class_name)
            if hasattr(cls, "_instance"):
                setattr(cls, "_instance", None)
    except (ImportError, AttributeError):
        pass


def _safe_reset_global_variable(module_name: str, variable_name: str) -> None:
    """
    Safely reset a global variable.

    Args:
        module_name: The module name
        variable_name: The variable name
    """
    try:
        module = importlib.import_module(module_name)
        if hasattr(module, variable_name):
            setattr(module, variable_name, None)
    except (ImportError, AttributeError):
        pass


def reset_all_singletons() -> None:
    """
    Reset all singletons for testing.

    This function resets all singleton instances in the registry,
    ensuring that tests start with a clean state.
    """
    # Reset all singletons in the registry
    registry = SingletonRegistry.get_instance()
    registry.reset()

    # Also reset the registry itself
    _safe_reset_class_instance(
        "src.infrastructure.patterns.singleton_registry", "SingletonRegistry"
    )

    # Reset any global singleton instances
    # This is for backward compatibility with old singleton implementations
    _safe_reset_global_variable(
        "src.infrastructure.aws.aws_client_singleton", "_aws_client_singleton_instance"
    )
    _safe_reset_class_instance(
        "src.infrastructure.config.manager", "ConfigurationManager"
    )
    _safe_reset_class_instance(
        "src.infrastructure.logging.logger_singleton", "LoggerSingleton"
    )


def reset_singleton(singleton_class: Type[Any]) -> None:
    """
    Reset a specific singleton for testing.

    This function resets a specific singleton instance in the registry,
    ensuring that tests start with a clean state for that singleton.

    Args:
        singleton_class: The singleton class to reset
    """
    # Reset the singleton in the registry
    registry = SingletonRegistry.get_instance()
    registry.reset(singleton_class)

    # Also reset any global singleton instance
    # This is for backward compatibility with old singleton implementations
    class_name = singleton_class.__name__
    if class_name == "AWSClient":
        _safe_reset_global_variable(
            "src.infrastructure.aws.aws_client_singleton",
            "_aws_client_singleton_instance",
        )
    elif class_name == "ConfigurationManager":
        _safe_reset_class_instance(
            "src.infrastructure.config.manager", "ConfigurationManager"
        )
    elif class_name == "LoggerSingleton":
        _safe_reset_class_instance(
            "src.infrastructure.logging.logger_singleton", "LoggerSingleton"
        )
