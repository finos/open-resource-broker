"""Behavior tests for ProvisioningOrchestrationService."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.ports.scheduler_port import SchedulerPort
from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
)
from orb.providers.base.strategy.provider_strategy import ProviderResult
from orb.domain.base.results import ProviderSelectionResult
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_async_submitted_create_is_not_retried():
    container = MagicMock()
    scheduler = MagicMock()
    scheduler.format_template_for_provider.return_value = {
        "template_id": "azure-vmss-test",
        "provider_api": "VMSS",
    }
    container.get.return_value = scheduler

    provider_selection_port = MagicMock()
    provider_selection_port.execute_operation = AsyncMock(
        return_value=ProviderResult.success_result(
            data={
                "resource_ids": ["vmss-azure-vmss-test-1234"],
                "instances": [],
            },
            metadata={
                "provider_data": {
                    "operation_status": "submitted",
                    "fulfillment_final": True,
                }
            },
        )
    )

    provider_config_port = MagicMock()
    config_port = MagicMock()
    config_port.get_request_config.return_value = {
        "fulfillment_max_retries": 3,
        "fulfillment_timeout_seconds": 300,
        "fulfillment_batch_size": 1000,
    }

    service = ProvisioningOrchestrationService(
        container=container,
        logger=MagicMock(),
        provider_selection_port=provider_selection_port,
        provider_config_port=provider_config_port,
        config_port=config_port,
        circuit_breaker_factory=lambda _key: MagicMock(has_state=MagicMock(return_value=False)),
    )

    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="azure-vmss-test",
        machine_count=1,
        provider_type="azure",
        provider_name="azure-default",
        request_id="req-12345678-1234-1234-1234-123456789012",
    )
    template = MagicMock()
    template.template_id = "azure-vmss-test"
    selection_result = ProviderSelectionResult(
        provider_type="azure",
        provider_name="azure-default",
        selection_reason="test",
    )

    result = await service.execute_provisioning(template, request, selection_result)

    assert result.success is True
    assert result.resource_ids == ["vmss-azure-vmss-test-1234"]
    assert result.instances == []
    assert result.is_final is True
    assert provider_selection_port.execute_operation.await_count == 1
    provider_config_port.get_provider_instance_config.assert_called_once_with("azure-default")
    container.get.assert_any_call(SchedulerPort)


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_planned_async_submitted_create_is_not_retried():
    container = MagicMock()
    scheduler = MagicMock()
    scheduler.format_template_for_provider.return_value = {
        "template_id": "azure-spot-placement-score-vmss",
        "provider_api": "VMSS",
        "allocation_strategy": "spotPlacementScore",
    }
    container.get.return_value = scheduler

    provider_selection_port = MagicMock()
    provider_selection_port.execute_operation = AsyncMock(
        return_value=ProviderResult.success_result(
            data={
                "resource_ids": ["vmss-azure-spot-placement-score-vmss-1234"],
                "instances": [],
            },
            metadata={
                "provider_data": {
                    "placement_plan": [
                        {
                            "candidate_id": "azure:eastus2:3:Standard_D2s_v5",
                            "planned_count": 1,
                        }
                    ],
                    "child_results": [
                        {
                            "candidate_id": "azure:eastus2:3:Standard_D2s_v5",
                            "requested_count": 1,
                            "success": True,
                            "resource_ids": ["vmss-azure-spot-placement-score-vmss-1234"],
                            "instances": [],
                        }
                    ],
                    "failed_subplans": [],
                    "unfulfilled_count": 0,
                    "terminal_error_message": None,
                    "fulfillment_final": True,
                }
            },
        )
    )

    provider_config_port = MagicMock()
    config_port = MagicMock()
    config_port.get_request_config.return_value = {
        "fulfillment_max_retries": 3,
        "fulfillment_timeout_seconds": 300,
        "fulfillment_batch_size": 1000,
    }

    service = ProvisioningOrchestrationService(
        container=container,
        logger=MagicMock(),
        provider_selection_port=provider_selection_port,
        provider_config_port=provider_config_port,
        config_port=config_port,
        circuit_breaker_factory=lambda _key: MagicMock(has_state=MagicMock(return_value=False)),
    )

    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="azure-spot-placement-score-vmss",
        machine_count=1,
        provider_type="azure",
        provider_name="azure-default",
        request_id="req-12345678-1234-1234-1234-123456789012",
    )
    template = MagicMock()
    template.template_id = "azure-spot-placement-score-vmss"
    selection_result = ProviderSelectionResult(
        provider_type="azure",
        provider_name="azure-default",
        selection_reason="test",
    )

    result = await service.execute_provisioning(template, request, selection_result)

    assert result.success is True
    assert result.resource_ids == ["vmss-azure-spot-placement-score-vmss-1234"]
    assert result.instances == []
    assert result.is_final is True
    assert result.provider_data == {
        "placement_plan": [
            {
                "candidate_id": "azure:eastus2:3:Standard_D2s_v5",
                "planned_count": 1,
            }
        ],
        "child_results": [
            {
                "candidate_id": "azure:eastus2:3:Standard_D2s_v5",
                "requested_count": 1,
                "success": True,
                "resource_ids": ["vmss-azure-spot-placement-score-vmss-1234"],
                "instances": [],
            }
        ],
        "failed_subplans": [],
        "unfulfilled_count": 0,
        "terminal_error_message": None,
        "fulfillment_final": True,
    }
    assert provider_selection_port.execute_operation.await_count == 1


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_provider_error_with_no_result_data_preserves_error_message():
    container = MagicMock()
    scheduler = MagicMock()
    scheduler.format_template_for_provider.return_value = {
        "template_id": "azure-single-vm-test",
        "provider_api": "SingleVM",
    }
    container.get.return_value = scheduler

    provider_selection_port = MagicMock()
    provider_selection_port.execute_operation = AsyncMock(
        return_value=ProviderResult.error_result(
            "Provisioning failed: insufficient capacity",
            "PROVISIONING_ADAPTER_ERROR",
            metadata={"provider_data": {"fleet_errors": [{"error_code": "AllocationFailed"}]}},
        )
    )

    provider_config_port = MagicMock()
    config_port = MagicMock()
    config_port.get_request_config.return_value = {
        "fulfillment_max_retries": 3,
        "fulfillment_timeout_seconds": 300,
        "fulfillment_batch_size": 1000,
    }

    service = ProvisioningOrchestrationService(
        container=container,
        logger=MagicMock(),
        provider_selection_port=provider_selection_port,
        provider_config_port=provider_config_port,
        config_port=config_port,
        circuit_breaker_factory=lambda _key: MagicMock(has_state=MagicMock(return_value=False)),
    )

    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="azure-single-vm-test",
        machine_count=1,
        provider_type="azure",
        provider_name="azure-default",
        request_id="req-12345678-1234-1234-1234-123456789012",
    )
    template = MagicMock()
    template.template_id = "azure-single-vm-test"
    selection_result = ProviderSelectionResult(
        provider_type="azure",
        provider_name="azure-default",
        selection_reason="test",
    )

    result = await service.execute_provisioning(template, request, selection_result)

    assert result.success is False
    assert result.error_message == "Provisioning failed: insufficient capacity"
    assert result.provider_data == {
        "fleet_errors": [{"error_code": "AllocationFailed"}]
    }


@pytest.mark.unit
@pytest.mark.application
@pytest.mark.asyncio
async def test_execute_provisioning_times_out_hung_provider_dispatch():
    container = MagicMock()
    scheduler = MagicMock()
    scheduler.format_template_for_provider.return_value = {
        "template_id": "azure-single-vm-test",
        "provider_api": "SingleVM",
    }
    container.get.return_value = scheduler

    provider_selection_port = MagicMock()

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(1)

    provider_selection_port.execute_operation = AsyncMock(side_effect=_hang)

    provider_config_port = MagicMock()
    config_port = MagicMock()
    config_port.get_request_config.return_value = {
        "fulfillment_max_retries": 3,
        "fulfillment_timeout_seconds": 0.01,
        "fulfillment_batch_size": 1000,
    }
    logger = MagicMock()

    service = ProvisioningOrchestrationService(
        container=container,
        logger=logger,
        provider_selection_port=provider_selection_port,
        provider_config_port=provider_config_port,
        config_port=config_port,
        circuit_breaker_factory=lambda _key: MagicMock(has_state=MagicMock(return_value=False)),
    )

    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="azure-single-vm-test",
        machine_count=1,
        provider_type="azure",
        provider_name="azure-default",
        request_id="req-12345678-1234-1234-1234-123456789012",
    )
    template = MagicMock()
    selection_result = ProviderSelectionResult(
        provider_type="azure",
        provider_name="azure-default",
        selection_reason="test",
    )

    result = await service.execute_provisioning(template, request, selection_result)

    assert result.success is False
    assert result.error_message == (
        "Provisioning operation timed out; provider submission status is unknown"
    )
    assert result.provider_data["operation_status"] == "timeout"
    assert result.provider_data["submission_status"] == "unknown"
    assert result.provider_data["timed_out"] is True
    logger.warning.assert_called()
