"""Utilities for resetting singletons during testing."""

import importlib
from typing import Any


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
                cls._instance = None
    except (ImportError, AttributeError):
        pass  # module or attribute absent in this environment; skip reset


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
        pass  # module or attribute absent in this environment; skip reset


def _reset_circuit_breaker_states() -> None:
    """Clear class-level circuit breaker state so tests start with closed circuits."""
    try:
        from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy

        CircuitBreakerStrategy._circuit_states.clear()
    except ImportError:
        pass


def reset_provider_registry() -> None:
    try:
        from orb.infrastructure.registry.base_registry import BaseRegistry

        BaseRegistry._instances.pop("ProviderRegistry", None)
    except ImportError:
        pass  # BaseRegistry not available; global variable reset below is sufficient
    _safe_reset_global_variable(
        "orb.providers.registry.provider_registry", "_provider_registry_instance"
    )


def reset_all_singletons() -> None:
    """
    Reset all singletons for testing.

    This function resets all singleton instances, ensuring that tests start
    with a clean state.
    """
    # Reset the DI container so dependency_overrides work correctly in FastAPI tests
    try:
        from orb.infrastructure.di.container import reset_container

        reset_container()
    except ImportError:
        pass  # DI container module may not be present in all test environments; skip reset

    # Reset circuit breaker shared state
    _reset_circuit_breaker_states()

    reset_provider_registry()


def reset_singleton(singleton_class: type[Any]) -> None:
    """
    Reset a specific singleton for testing.

    Args:
        singleton_class: The singleton class to reset
    """
    # No known singleton classes require explicit reset here; the reset_all_singletons()
    # path handles DI container + circuit breaker + provider registry globally.
    pass
