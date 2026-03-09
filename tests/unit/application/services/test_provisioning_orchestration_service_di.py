"""Tests for ProvisioningOrchestrationService DI wiring and constructor contract."""

import inspect
import ast
import textwrap
from unittest.mock import MagicMock

import pytest

from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
)
from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy


# ---------------------------------------------------------------------------
# Task 1716 — circuit_breaker_factory is a required constructor parameter
# ---------------------------------------------------------------------------


class TestCircuitBreakerFactoryRequired:
    """ProvisioningOrchestrationService must not be constructable without circuit_breaker_factory."""

    def _minimal_kwargs(self):
        return dict(
            container=MagicMock(),
            logger=MagicMock(),
            provider_selection_port=MagicMock(),
            provider_config_port=MagicMock(),
            config_port=MagicMock(),
        )

    def test_raises_type_error_when_circuit_breaker_factory_omitted(self):
        with pytest.raises(TypeError):
            ProvisioningOrchestrationService(**self._minimal_kwargs())

    def test_constructs_successfully_when_circuit_breaker_factory_provided(self):
        svc = ProvisioningOrchestrationService(
            **self._minimal_kwargs(),
            circuit_breaker_factory=CircuitBreakerStrategy,
        )
        assert svc is not None

    def test_circuit_breaker_factory_has_no_default(self):
        sig = inspect.signature(ProvisioningOrchestrationService.__init__)
        param = sig.parameters["circuit_breaker_factory"]
        assert param.default is inspect.Parameter.empty, (
            "circuit_breaker_factory must be a required parameter with no default"
        )


# ---------------------------------------------------------------------------
# Task 1715 — DI wiring passes CircuitBreakerStrategy as circuit_breaker_factory
# ---------------------------------------------------------------------------


class TestDIWiringCircuitBreakerFactory:
    """The DI registration for ProvisioningOrchestrationService wires CircuitBreakerStrategy."""

    def test_factory_function_passes_circuit_breaker_strategy(self):
        """
        Parse infrastructure_services.py with AST and verify that the factory
        function for ProvisioningOrchestrationService passes CircuitBreakerStrategy
        as the circuit_breaker_factory keyword argument.

        This avoids spinning up the full DI container while still asserting the
        actual wiring code rather than a mock.
        """
        import importlib.util
        import pathlib

        src = pathlib.Path(
            "/Users/flamurg/src/aws/symphony/open-resource-broker"
            "/src/orb/infrastructure/di/infrastructure_services.py"
        ).read_text()

        tree = ast.parse(src)

        # Find the ProvisioningOrchestrationService(...) call inside the factory
        found_kwarg = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match calls whose function name ends with ProvisioningOrchestrationService
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else (func.id if isinstance(func, ast.Name) else None)
            )
            if name != "ProvisioningOrchestrationService":
                continue
            for kw in node.keywords:
                if kw.arg == "circuit_breaker_factory":
                    # The value should be the bare name CircuitBreakerStrategy
                    if isinstance(kw.value, ast.Name) and kw.value.id == "CircuitBreakerStrategy":
                        found_kwarg = True

        assert found_kwarg, (
            "Expected _register_provisioning_orchestration_service to pass "
            "circuit_breaker_factory=CircuitBreakerStrategy to ProvisioningOrchestrationService"
        )

    def test_factory_callable_produces_service_with_circuit_breaker_strategy(self):
        """
        Call the factory function directly with a mock container and verify the
        returned service stores CircuitBreakerStrategy as its factory callable.
        """
        from orb.infrastructure.di.infrastructure_services import (
            _register_provisioning_orchestration_service,
        )
        from orb.infrastructure.di.container import DIContainer
        from orb.domain.base.ports import (
            ConfigurationPort,
            ContainerPort,
            LoggingPort,
            ProviderConfigPort,
        )
        from orb.domain.base.ports.provider_selection_port import ProviderSelectionPort

        container = DIContainer()

        # Register the minimal stubs the factory needs
        container.register_instance(ContainerPort, MagicMock(spec=ContainerPort))
        container.register_instance(LoggingPort, MagicMock(spec=LoggingPort))
        container.register_instance(ProviderSelectionPort, MagicMock(spec=ProviderSelectionPort))
        container.register_instance(ProviderConfigPort, MagicMock(spec=ProviderConfigPort))
        container.register_instance(ConfigurationPort, MagicMock(spec=ConfigurationPort))

        _register_provisioning_orchestration_service(container)

        svc = container.get(ProvisioningOrchestrationService)

        assert svc._circuit_breaker_factory is CircuitBreakerStrategy, (
            "DI container must wire CircuitBreakerStrategy as circuit_breaker_factory"
        )
