"""Tests that routing telemetry flows from strategy routing_info to ProvisioningResult.provider_data.

Verifies the full path:
  strategy returns routing_info → app layer stamps → ProvisioningResult.provider_data has the fields
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
)
from orb.domain.base.results import ProviderSelectionResult
from orb.providers.base.strategy.provider_strategy import ProviderResult


def _make_service() -> ProvisioningOrchestrationService:
    container = MagicMock()
    logger = MagicMock()
    provider_selection_port = MagicMock()
    provider_config_port = MagicMock()
    config_port = MagicMock()
    circuit_breaker_factory = MagicMock()

    config_port.get_request_config.return_value = {}

    cb = MagicMock()
    cb.has_state.return_value = False
    circuit_breaker_factory.return_value = cb

    return ProvisioningOrchestrationService(
        container=container,
        logger=logger,
        provider_selection_port=provider_selection_port,
        provider_config_port=provider_config_port,
        config_port=config_port,
        circuit_breaker_factory=circuit_breaker_factory,
    )


def _make_request(count: int = 1):
    request = MagicMock()
    request.request_id = "req-123"
    request.requested_count = count
    request.metadata = {}
    request.update_metadata = lambda d: request
    return request


def _make_template():
    template = MagicMock()
    template.template_id = "tmpl-1"
    return template


def _make_selection_result(provider_name: str = "aws_default_us-east-1") -> ProviderSelectionResult:
    return ProviderSelectionResult(
        provider_name=provider_name,
        provider_type="aws",
        selection_reason="test",
        confidence=1.0,
    )


@pytest.mark.asyncio
async def test_routing_info_from_aws_strategy_reaches_provider_data():
    """routing_info set by AWSProviderStrategy ends up in ProvisioningResult.provider_data."""
    svc = _make_service()

    # Simulate a ProviderResult with routing_info as AWSProviderStrategy would produce
    provider_result = ProviderResult.success_result(
        data={"resource_ids": ["i-abc"], "instances": [{"id": "i-abc"}], "instance_ids": ["i-abc"]},
        metadata={"dry_run": False},
    ).model_copy(
        update={
            "routing_info": {
                "execution_time_ms": 42,
                "provider": "aws",
            }
        }
    )

    svc._provider_selection_port.execute_operation = AsyncMock(return_value=provider_result)

    # Patch scheduler
    scheduler = MagicMock()
    scheduler.format_template_for_provider.return_value = {}
    svc._container.get.return_value = scheduler

    result = await svc._dispatch_single_attempt(
        _make_template(), _make_request(), _make_selection_result(), 1
    )

    assert result.success is True
    assert result.provider_data["execution_time_ms"] == 42
    assert result.provider_data["provider"] == "aws"
    # metadata fields also present
    assert result.provider_data["dry_run"] is False


@pytest.mark.asyncio
async def test_routing_info_from_fallback_strategy_reaches_provider_data():
    """routing_info set by FallbackProviderStrategy ends up in ProvisioningResult.provider_data."""
    svc = _make_service()

    provider_result = ProviderResult.success_result(
        data={"resource_ids": ["i-xyz"], "instances": [{"id": "i-xyz"}], "instance_ids": ["i-xyz"]},
        metadata={},
    ).model_copy(
        update={
            "routing_info": {
                "fallback_mode": "immediate",
                "total_execution_time_ms": 123.4,
                "active_strategy": "aws",
                "circuit_state": "closed",
            }
        }
    )

    svc._provider_selection_port.execute_operation = AsyncMock(return_value=provider_result)

    scheduler = MagicMock()
    scheduler.format_template_for_provider.return_value = {}
    svc._container.get.return_value = scheduler

    result = await svc._dispatch_single_attempt(
        _make_template(), _make_request(), _make_selection_result(), 1
    )

    assert result.success is True
    assert result.provider_data["fallback_mode"] == "immediate"
    assert result.provider_data["active_strategy"] == "aws"
    assert result.provider_data["circuit_state"] == "closed"


@pytest.mark.asyncio
async def test_routing_info_from_composite_strategy_reaches_provider_data():
    """routing_info set by CompositeProviderStrategy ends up in ProvisioningResult.provider_data."""
    svc = _make_service()

    provider_result = ProviderResult.success_result(
        data={
            "resource_ids": ["i-comp"],
            "instances": [{"id": "i-comp"}],
            "instance_ids": ["i-comp"],
        },
        metadata={},
    ).model_copy(
        update={
            "routing_info": {
                "composition_mode": "parallel",
                "total_execution_time_ms": 88.0,
                "strategies_executed": 2,
                "successful_strategies": 2,
                "aggregation_policy": "merge_all",
            }
        }
    )

    svc._provider_selection_port.execute_operation = AsyncMock(return_value=provider_result)

    scheduler = MagicMock()
    scheduler.format_template_for_provider.return_value = {}
    svc._container.get.return_value = scheduler

    result = await svc._dispatch_single_attempt(
        _make_template(), _make_request(), _make_selection_result(), 1
    )

    assert result.success is True
    assert result.provider_data["composition_mode"] == "parallel"
    assert result.provider_data["strategies_executed"] == 2
    assert result.provider_data["successful_strategies"] == 2


@pytest.mark.asyncio
async def test_no_routing_info_does_not_break_provider_data():
    """When routing_info is None, provider_data still contains metadata fields."""
    svc = _make_service()

    provider_result = ProviderResult.success_result(
        data={
            "resource_ids": ["i-plain"],
            "instances": [{"id": "i-plain"}],
            "instance_ids": ["i-plain"],
        },
        metadata={"some_key": "some_value"},
    )
    # routing_info is None by default

    svc._provider_selection_port.execute_operation = AsyncMock(return_value=provider_result)

    scheduler = MagicMock()
    scheduler.format_template_for_provider.return_value = {}
    svc._container.get.return_value = scheduler

    result = await svc._dispatch_single_attempt(
        _make_template(), _make_request(), _make_selection_result(), 1
    )

    assert result.success is True
    assert result.provider_data["some_key"] == "some_value"
