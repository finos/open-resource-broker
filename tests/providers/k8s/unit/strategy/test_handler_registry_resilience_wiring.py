"""Unit tests for K8sHandlerRegistry resilience-knob threading.

Proves that the circuit-breaker and retry knobs declared on
:class:`K8sProviderConfig` (``circuit_breaker_failure_threshold``,
``circuit_breaker_reset_timeout``, ``max_retries``, ``retry_base_delay``,
``retry_max_delay``) are threaded through
:meth:`K8sHandlerRegistry.get_handler` into the constructed handler so the
handler's ``with_retry`` layer actually uses the configured values rather than
the K8sHandlerBase hardcoded defaults.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.resilience import MaxRetriesExceededError
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry


def _make_registry(
    config: K8sProviderConfig,
    *,
    plugin_factories: dict[str, Any] | None = None,
) -> K8sHandlerRegistry:
    """Build a registry with a configured value; no cluster wiring needed."""
    return K8sHandlerRegistry(
        config=config,
        logger=MagicMock(),
        client_provider=MagicMock,
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: dict(plugin_factories or {}),
        native_spec_service_provider=lambda: None,
    )


class TestResilienceKnobThreading:
    """Configured knobs must reach the built-in handler constructors."""

    def test_configured_failure_threshold_reaches_handler(self) -> None:
        config = K8sProviderConfig(circuit_breaker_failure_threshold=11)  # type: ignore[call-arg]
        registry = _make_registry(config)

        handler = registry.get_handler("Pod")

        assert handler._cb_failure_threshold == 11

    def test_all_five_knobs_reach_handler(self) -> None:
        config = K8sProviderConfig(  # type: ignore[call-arg]
            circuit_breaker_failure_threshold=7,
            circuit_breaker_reset_timeout=99,
            max_retries=9,
            retry_base_delay=2.5,
            retry_max_delay=45.0,
        )
        registry = _make_registry(config)

        handler = registry.get_handler("Deployment")

        assert handler._cb_failure_threshold == 7
        assert handler._cb_reset_timeout == 99
        assert handler._max_retries == 9
        assert handler._base_delay == 2.5
        assert handler._max_delay == 45.0

    def test_defaults_preserved_when_config_unset(self) -> None:
        """An unconfigured config yields the K8sHandlerBase defaults (no-op change)."""
        registry = _make_registry(K8sProviderConfig())  # type: ignore[call-arg]

        handler = registry.get_handler("Job")

        assert handler._cb_failure_threshold == 5
        assert handler._cb_reset_timeout == 60
        assert handler._max_retries == 3
        assert handler._base_delay == 1.0
        assert handler._max_delay == 30.0

    def test_knobs_feed_the_retry_decorator(self) -> None:
        """The configured max_retries must reach the ``with_retry`` budget.

        Drives ``with_retry`` against an always-failing recoverable operation
        and asserts the operation is invoked ``max_retries + 1`` times (initial
        attempt plus the configured retries), proving the config value flows all
        the way into the retry decorator rather than the hardcoded default of 3.
        """
        from orb.infrastructure.resilience.strategy.circuit_breaker import (
            CircuitBreakerStrategy,
        )

        # The circuit-breaker state is class-level and keyed by service name
        # ("kubernetes.pod").  Clear it so accumulated failures from other tests
        # in the session cannot pre-open the breaker and short-circuit the
        # retry-budget assertion below.
        CircuitBreakerStrategy._circuit_states.pop("kubernetes.pod", None)

        config = K8sProviderConfig(  # type: ignore[call-arg]
            max_retries=2,
            retry_base_delay=0.01,
            retry_max_delay=0.01,
        )
        registry = _make_registry(config)
        handler = registry.get_handler("Pod")

        calls = {"n": 0}

        def _always_fails() -> None:
            calls["n"] += 1
            # 503 is a recoverable status the exponential strategy retries.
            raise _ApiException503()

        # Budget exhaustion surfaces MaxRetriesExceededError after the final
        # attempt; the assertion below then verifies the invocation count.
        with pytest.raises(MaxRetriesExceededError):
            handler.with_retry(_always_fails, operation_name="unit_probe")

        # initial attempt + 2 configured retries == 3 invocations
        assert calls["n"] == 3


class TestPluginPathThreading:
    """Plugin-registered factories receive the same resilience knobs."""

    def test_plugin_factory_receives_resilience_kwargs(self) -> None:
        captured: dict[str, Any] = {}

        def _factory(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return MagicMock()

        config = K8sProviderConfig(  # type: ignore[call-arg]
            circuit_breaker_failure_threshold=13,
            max_retries=6,
        )
        registry = _make_registry(config, plugin_factories={"Custom": _factory})

        registry.get_handler("Custom")

        assert captured["circuit_breaker_failure_threshold"] == 13
        assert captured["max_retries"] == 6
        assert captured["circuit_breaker_reset_timeout"] == 60
        assert captured["base_delay"] == 1.0
        assert captured["max_delay"] == 30.0


class _ApiException503(Exception):
    """Minimal stand-in carrying a 503 ``status`` for retry classification."""

    status = 503

    def __init__(self) -> None:
        super().__init__("service unavailable")
