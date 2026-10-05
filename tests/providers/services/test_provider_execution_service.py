"""Unit tests for ProviderExecutionService.

Covers the registry-based strategy execution path: strategy lookup,
initialization, capability checks, operation execution, metrics
recording, and the health-check / config-lookup helper methods.
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from orb.domain.base.ports import ConfigurationPort, LoggingPort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
from orb.providers.base.metrics import ProviderMetricsPort
from orb.providers.base.strategy.provider_strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)
from orb.providers.services.provider_execution_service import ProviderExecutionService


class FakeStrategy(ProviderStrategy):
    """Minimal concrete ProviderStrategy with per-instance configurable behaviour."""

    def __init__(
        self,
        provider_type: str = "fake",
        *,
        operation_result: Optional[ProviderResult] = None,
        operation_raises: Optional[Exception] = None,
        supported_ops: Optional[list[ProviderOperationType]] = None,
        init_result: bool = True,
        health_raises: Optional[Exception] = None,
    ) -> None:
        from orb.infrastructure.interfaces.provider import BaseProviderConfig

        super().__init__(BaseProviderConfig(provider_type=provider_type))
        self._provider_type = provider_type
        self._operation_result = operation_result or ProviderResult.success_result({"ok": True})
        self._operation_raises = operation_raises
        self._supported_ops = (
            supported_ops if supported_ops is not None else list(ProviderOperationType)
        )
        self._init_result = init_result
        self._health_raises = health_raises

    @property
    def provider_type(self) -> str:
        return self._provider_type

    def initialize(self) -> bool:
        self._initialized = self._init_result
        return self._init_result

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        if self._operation_raises is not None:
            raise self._operation_raises
        return self._operation_result

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=self._provider_type, supported_operations=self._supported_ops
        )

    def check_health(self) -> ProviderHealthStatus:
        if self._health_raises is not None:
            raise self._health_raises
        return ProviderHealthStatus.healthy("ok", response_time_ms=1.0)

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        return self._provider_type

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        return {"provider_type": provider_name}

    def get_provider_name_pattern(self) -> str:
        return self._provider_type

    def cleanup(self) -> None:
        self._initialized = False


def make_op(
    op_type: ProviderOperationType = ProviderOperationType.HEALTH_CHECK,
) -> ProviderOperation:
    return ProviderOperation(operation_type=op_type, parameters={})


@pytest.fixture
def mock_logger() -> MagicMock:
    return MagicMock(spec=LoggingPort)


@pytest.fixture
def mock_config_port() -> MagicMock:
    return MagicMock(spec=ConfigurationPort)


@pytest.fixture
def mock_registry() -> MagicMock:
    return MagicMock(spec=ProviderRegistryPort)


@pytest.fixture
def mock_metrics() -> MagicMock:
    return MagicMock(spec=ProviderMetricsPort)


@pytest.fixture
def service(mock_logger, mock_config_port, mock_registry, mock_metrics) -> ProviderExecutionService:
    return ProviderExecutionService(
        logger=mock_logger,
        config_port=mock_config_port,
        registry=mock_registry,
        metrics=mock_metrics,
    )


@pytest.mark.unit
class TestProviderExecutionServiceDefaultMetrics:
    def test_defaults_to_noop_metrics_when_none_passed(
        self, mock_logger, mock_config_port, mock_registry
    ):
        from orb.providers.base.metrics import NoOpProviderMetrics

        svc = ProviderExecutionService(
            logger=mock_logger, config_port=mock_config_port, registry=mock_registry
        )
        assert isinstance(svc._metrics, NoOpProviderMetrics)


@pytest.mark.unit
class TestExecuteOperationSuccess:
    @pytest.mark.asyncio
    async def test_executes_and_returns_strategy_result(
        self, service, mock_registry, mock_config_port, mock_metrics
    ):
        strategy = FakeStrategy("aws", operation_result=ProviderResult.success_result({"a": 1}))
        mock_registry.is_provider_instance_registered.return_value = False
        mock_registry.is_provider_registered.return_value = True
        mock_registry.get_or_create_strategy.return_value = strategy
        mock_config_port.get_provider_instance_config.return_value = None

        result = await service.execute_operation("aws", make_op())

        assert result.success
        assert result.data == {"a": 1}
        mock_metrics.record_operation.assert_called_once()
        _, kwargs = mock_metrics.record_operation.call_args
        assert kwargs["success"] is True
        assert kwargs["service"] == "aws"

    @pytest.mark.asyncio
    async def test_prefers_instance_registration_over_type(
        self, service, mock_registry, mock_config_port
    ):
        strategy = FakeStrategy("inst-1")
        mock_registry.is_provider_instance_registered.return_value = True
        mock_registry.is_provider_registered.return_value = True
        mock_registry.get_or_create_strategy.return_value = strategy
        mock_config_port.get_provider_instance_config.return_value = None

        result = await service.execute_operation("inst-1", make_op())

        assert result.success
        # is_provider_registered should not even need to be consulted once
        # the instance branch already resolved a strategy.
        mock_registry.get_or_create_strategy.assert_called_once_with("inst-1", {})

    @pytest.mark.asyncio
    async def test_does_not_reinitialize_already_initialized_strategy(
        self, service, mock_registry, mock_config_port
    ):
        strategy = FakeStrategy("already-init")
        strategy.initialize()
        assert strategy.is_initialized
        mock_registry.is_provider_instance_registered.return_value = True
        mock_registry.get_or_create_strategy.return_value = strategy
        mock_config_port.get_provider_instance_config.return_value = None

        result = await service.execute_operation("already-init", make_op())

        assert result.success


@pytest.mark.unit
class TestExecuteOperationStrategyNotFound:
    @pytest.mark.asyncio
    async def test_returns_error_when_strategy_cannot_be_created(
        self, service, mock_registry, mock_metrics
    ):
        mock_registry.is_provider_instance_registered.return_value = False
        mock_registry.is_provider_registered.return_value = False

        result = await service.execute_operation("unknown", make_op())

        assert not result.success
        assert result.error_code == "STRATEGY_NOT_FOUND"
        # No strategy was created, so no metrics should be recorded for this path.
        mock_metrics.record_operation.assert_not_called()


@pytest.mark.unit
class TestExecuteOperationInitializationFailure:
    @pytest.mark.asyncio
    async def test_returns_error_when_initialize_fails(
        self, service, mock_registry, mock_config_port
    ):
        strategy = FakeStrategy("bad-init", init_result=False)
        mock_registry.is_provider_instance_registered.return_value = True
        mock_registry.get_or_create_strategy.return_value = strategy
        mock_config_port.get_provider_instance_config.return_value = None

        result = await service.execute_operation("bad-init", make_op())

        assert not result.success
        assert result.error_code == "STRATEGY_INITIALIZATION_FAILED"


@pytest.mark.unit
class TestExecuteOperationUnsupportedOperation:
    @pytest.mark.asyncio
    async def test_returns_error_and_records_metrics_when_operation_unsupported(
        self, service, mock_registry, mock_config_port, mock_metrics
    ):
        strategy = FakeStrategy("limited", supported_ops=[ProviderOperationType.HEALTH_CHECK])
        mock_registry.is_provider_instance_registered.return_value = True
        mock_registry.get_or_create_strategy.return_value = strategy
        mock_config_port.get_provider_instance_config.return_value = None

        result = await service.execute_operation(
            "limited", make_op(ProviderOperationType.CREATE_INSTANCES)
        )

        assert not result.success
        assert result.error_code == "OPERATION_NOT_SUPPORTED"
        mock_metrics.record_operation.assert_called_once()
        _, kwargs = mock_metrics.record_operation.call_args
        assert kwargs["success"] is False
        assert kwargs["error_code"] == "OPERATION_NOT_SUPPORTED"


@pytest.mark.unit
class TestExecuteOperationExceptionHandling:
    @pytest.mark.asyncio
    async def test_strategy_raising_is_caught_and_recorded(
        self, service, mock_registry, mock_config_port, mock_metrics, mock_logger
    ):
        strategy = FakeStrategy("boom", operation_raises=RuntimeError("kaboom"))
        mock_registry.is_provider_instance_registered.return_value = True
        mock_registry.get_or_create_strategy.return_value = strategy
        mock_config_port.get_provider_instance_config.return_value = None

        result = await service.execute_operation("boom", make_op())

        assert not result.success
        assert result.error_code == "EXECUTION_ERROR"
        assert "kaboom" in (result.error_message or "")
        mock_metrics.record_operation.assert_called_once()
        _, kwargs = mock_metrics.record_operation.call_args
        assert kwargs["error_code"] == "EXECUTION_ERROR"
        mock_logger.error.assert_called()


@pytest.mark.unit
class TestGetStrategyCapabilities:
    def test_returns_capabilities_for_known_strategy(
        self, service, mock_registry, mock_config_port
    ):
        strategy = FakeStrategy("cap-provider")
        mock_registry.is_provider_instance_registered.return_value = True
        mock_registry.get_or_create_strategy.return_value = strategy
        mock_config_port.get_provider_instance_config.return_value = None

        caps = service.get_strategy_capabilities("cap-provider")

        assert caps is not None
        assert caps.provider_type == "cap-provider"

    def test_returns_none_for_unknown_strategy(self, service, mock_registry):
        mock_registry.is_provider_instance_registered.return_value = False
        mock_registry.is_provider_registered.return_value = False

        caps = service.get_strategy_capabilities("missing")

        assert caps is None


@pytest.mark.unit
class TestCheckStrategyHealth:
    def test_returns_health_status_and_records_counter(
        self, service, mock_registry, mock_config_port, mock_metrics
    ):
        strategy = FakeStrategy("healthy-provider")
        mock_registry.is_provider_instance_registered.return_value = True
        mock_registry.get_or_create_strategy.return_value = strategy
        mock_config_port.get_provider_instance_config.return_value = None

        status = service.check_strategy_health("healthy-provider")

        assert status is not None
        assert status.is_healthy
        mock_metrics.record_counter.assert_called_once_with(
            "provider.strategy.health_checks.total",
            labels={"provider_id": "healthy-provider"},
        )

    def test_returns_none_when_strategy_not_found(self, service, mock_registry):
        mock_registry.is_provider_instance_registered.return_value = False
        mock_registry.is_provider_registered.return_value = False

        status = service.check_strategy_health("missing")

        assert status is None

    def test_catches_exception_from_check_health_and_returns_unhealthy(
        self, service, mock_registry, mock_config_port, mock_logger
    ):
        strategy = FakeStrategy("flaky", health_raises=RuntimeError("health boom"))
        mock_registry.is_provider_instance_registered.return_value = True
        mock_registry.get_or_create_strategy.return_value = strategy
        mock_config_port.get_provider_instance_config.return_value = None

        status = service.check_strategy_health("flaky")

        assert status is not None
        assert not status.is_healthy
        assert "health boom" in status.status_message
        mock_logger.error.assert_called()


@pytest.mark.unit
class TestCreateStrategyErrorHandling:
    def test_registry_exception_is_caught_and_logged(self, service, mock_registry, mock_logger):
        mock_registry.is_provider_instance_registered.side_effect = RuntimeError("registry down")

        strategy = service._create_strategy("whatever")

        assert strategy is None
        mock_logger.error.assert_called()


@pytest.mark.unit
class TestGetProviderConfig:
    def test_returns_empty_dict_when_no_instance_config(self, service, mock_config_port):
        mock_config_port.get_provider_instance_config.return_value = None

        config = service._get_provider_config("some-provider")

        assert config == {}

    def test_returns_config_dict_from_instance_config(self, service, mock_config_port):
        instance_config = MagicMock()
        instance_config.config = {"region": "us-east-1"}
        mock_config_port.get_provider_instance_config.return_value = instance_config

        config = service._get_provider_config("some-provider")

        assert config == {"region": "us-east-1"}

    def test_swallows_exception_and_returns_empty_dict(
        self, service, mock_config_port, mock_logger
    ):
        mock_config_port.get_provider_instance_config.side_effect = RuntimeError("config boom")

        config = service._get_provider_config("broken-provider")

        assert config == {}
        mock_logger.warning.assert_called()
