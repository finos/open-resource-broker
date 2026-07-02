"""Unit tests for get_or_create_strategy type-level fallback behaviour.

Covers:
- k8s instance unknown + no config → fallback raises inside factory → registry returns None
- aws instance unknown + no config → fallback boots a strategy from boto3 defaults
"""

from __future__ import annotations

import threading
from typing import cast
from unittest.mock import MagicMock

from orb.providers.registry.provider_registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bare_registry() -> ProviderRegistry:
    """Return a fresh ProviderRegistry with an empty internal state."""
    registry = cast(ProviderRegistry, ProviderRegistry())
    registry._strategy_cache = {}
    registry._health_states = {}
    registry._fallback_strategy = None
    registry._registry_lock = threading.RLock()
    registry._type_registrations = {}
    registry._instance_registrations = {}
    registry._config_port = None
    registry._logger = MagicMock()
    return registry


# ---------------------------------------------------------------------------
# k8s fallback: unknown instance + no config → None (not an exception)
# ---------------------------------------------------------------------------


def test_get_or_create_strategy_k8s_fallback_no_config_returns_none() -> None:
    """When a k8s instance name is looked up but has no registered config, the
    type-level fallback factory raises (empty config → wrong-cluster risk) and
    get_or_create_strategy must return None rather than propagating the error.
    """
    registry = _bare_registry()

    # Register the k8s type with a factory that raises for empty config
    # (mirrors what create_k8s_strategy does in production)
    def _k8s_factory(cfg):
        if cfg is None or (isinstance(cfg, dict) and not cfg):
            raise RuntimeError(
                "Cannot create a Kubernetes strategy without explicit cluster-targeting configuration"
            )
        mock_strategy = MagicMock()
        mock_strategy.is_initialized = True
        return mock_strategy

    registry.register_provider(
        provider_type="k8s",
        strategy_factory=_k8s_factory,
        config_factory=MagicMock(),
    )

    # "k8s-prod" is NOT registered as an instance — triggers the type-level fallback
    result = registry.get_or_create_strategy("k8s-prod", config=None)

    assert result is None, (
        f"Expected None when k8s fallback factory rejects empty config, got {result!r}"
    )
    # The registry must log a warning so operators know the fallback was skipped
    registry._logger.warning.assert_called()
    warning_text = str(registry._logger.warning.call_args)
    assert "k8s-prod" in warning_text


def test_get_or_create_strategy_k8s_fallback_logs_provider_type() -> None:
    """The warning emitted by the fallback guard must mention both the instance
    name and the provider type so the operator can correlate the log line."""
    registry = _bare_registry()

    def _always_raise(cfg):
        raise RuntimeError("no cluster config")

    registry.register_provider(
        provider_type="k8s",
        strategy_factory=_always_raise,
        config_factory=MagicMock(),
    )

    registry.get_or_create_strategy("k8s-staging", config=None)

    registry._logger.warning.assert_called()
    call_args = registry._logger.warning.call_args
    # First arg is the format string; positional args after that fill the %r slots
    positional = call_args.args
    assert "k8s-staging" in positional, "Instance name must appear in warning args"
    assert "k8s" in positional, "Provider type must appear in warning args"


# ---------------------------------------------------------------------------
# AWS fallback: unknown instance + no config → strategy boots successfully
# (regression guard — ensure the guard only blocks k8s)
# ---------------------------------------------------------------------------


def test_get_or_create_strategy_aws_fallback_no_config_boots_strategy() -> None:
    """When an AWS instance name is unknown the type-level fallback must still
    produce a working strategy — AWS reads credentials from the boto3 chain so
    an empty config is safe.
    """
    registry = _bare_registry()

    mock_strategy = MagicMock()
    mock_strategy.is_initialized = True
    mock_strategy.initialize.return_value = True

    def _aws_factory(cfg):
        # AWS is tolerant of None/empty config (boto3 credential chain)
        return mock_strategy

    registry.register_provider(
        provider_type="aws",
        strategy_factory=_aws_factory,
        config_factory=MagicMock(),
    )

    # "aws-old-instance" is NOT registered — triggers type-level fallback
    result = registry.get_or_create_strategy("aws-old-instance", config=None)

    assert result is mock_strategy, (
        "AWS type-level fallback must return a usable strategy even with no config"
    )
    assert "aws-old-instance" in registry._strategy_cache


# ---------------------------------------------------------------------------
# New coverage: requested gaps
# ---------------------------------------------------------------------------


def test_unknown_instance_known_type_creates_type_strategy() -> None:
    """Asking for an instance name that is not registered as an instance, but
    whose type prefix IS registered as a type, returns a type-level strategy."""
    registry = _bare_registry()

    type_strategy = MagicMock()
    type_strategy.is_initialized = True

    def _factory(cfg):
        return type_strategy

    registry.register_provider(
        provider_type="aws",
        strategy_factory=_factory,
        config_factory=MagicMock(),
    )

    # "aws_someprofile_someregion" is NOT registered as an instance.
    result = registry.get_or_create_strategy("aws_someprofile_someregion")

    assert result is type_strategy


def test_unknown_instance_unknown_type_returns_none() -> None:
    """When neither the instance nor its inferred type is registered,
    get_or_create_strategy returns None."""
    registry = _bare_registry()

    result = registry.get_or_create_strategy("gcp_unknown_instance")

    assert result is None


def test_fallback_logs_warning_with_instance_and_type_names() -> None:
    """When the type-level fallback path is used, the registry logs a message
    that contains both the instance identifier and the inferred type name."""
    registry = _bare_registry()

    type_strategy = MagicMock()
    type_strategy.is_initialized = True

    registry.register_provider(
        provider_type="aws",
        strategy_factory=lambda _cfg: type_strategy,
        config_factory=MagicMock(),
    )

    registry.get_or_create_strategy("aws_someprofile_someregion")

    # The fallback path logs via logger.info (see provider_registry.py).
    all_info_calls = [str(c) for c in registry._logger.info.call_args_list]
    assert any("aws_someprofile_someregion" in msg for msg in all_info_calls), (
        f"Expected instance name in log; got: {all_info_calls}"
    )
    assert any("aws" in msg for msg in all_info_calls), (
        f"Expected type name in log; got: {all_info_calls}"
    )


def test_known_instance_takes_priority_over_type_fallback() -> None:
    """When an exact instance registration exists, the instance-level strategy
    is returned — the type-level fallback is never reached."""
    registry = _bare_registry()

    type_strategy = MagicMock()
    type_strategy.is_initialized = True
    instance_strategy = MagicMock()
    instance_strategy.is_initialized = True

    registry.register_provider(
        provider_type="aws",
        strategy_factory=lambda _cfg: type_strategy,
        config_factory=MagicMock(),
    )
    registry.register_provider_instance(
        provider_type="aws",
        instance_name="aws_us_east_1",
        strategy_factory=lambda _cfg: instance_strategy,
        config_factory=MagicMock(),
    )

    result = registry.get_or_create_strategy("aws_us_east_1")

    assert result is instance_strategy
    assert result is not type_strategy
