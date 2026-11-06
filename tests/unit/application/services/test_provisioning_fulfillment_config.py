"""Per-request ``fulfillment_config`` overrides keep overlaying the configured defaults."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
    ProvisioningResult,
)
from orb.domain.base.results import ProviderSelectionResult
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType

_SELECTION = ProviderSelectionResult(
    provider_name="aws-default",
    provider_type="aws",
    selection_reason="test",
    confidence=1.0,
)


def _service(request_config: dict) -> ProvisioningOrchestrationService:
    config_port = MagicMock()
    config_port.get_request_config.return_value = request_config
    service = ProvisioningOrchestrationService(
        uow_factory=MagicMock(),
        scheduler=MagicMock(),
        logger=MagicMock(),
        provider_selection_port=MagicMock(),
        provider_config_port=MagicMock(),
        config_port=config_port,
        circuit_breaker_factory=MagicMock(),
    )
    service._dispatch_single_attempt = AsyncMock(  # type: ignore[method-assign]
        return_value=ProvisioningResult(
            success=True,
            resource_ids=[],
            machine_ids=[],
            instances=[],
            provider_data={},
            fulfilled_count=0,
            is_final=True,
        )
    )
    return service


def _request(fulfillment_config: dict | None = None) -> Request:
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl",
        machine_count=2,
        provider_type="aws",
        provider_name="aws-default",
    )
    if fulfillment_config is not None:
        request = request.update_metadata({"fulfillment_config": fulfillment_config})
    return request


@pytest.mark.asyncio
async def test_configured_dispatch_timeout_applies_without_an_override():
    service = _service({"dispatch_timeout_seconds": 77})

    await service.execute_provisioning(MagicMock(), _request(), _SELECTION)

    assert service._dispatch_single_attempt.await_args.args[4] == 77.0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_request_override_replaces_the_configured_dispatch_timeout():
    service = _service({"dispatch_timeout_seconds": 77})

    await service.execute_provisioning(
        MagicMock(), _request({"dispatch_timeout_seconds": "42"}), _SELECTION
    )

    assert service._dispatch_single_attempt.await_args.args[4] == 42.0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_request_override_of_the_provisioning_timeout_applies():
    service = _service({"fulfillment_timeout_seconds": 300})

    await service.execute_provisioning(MagicMock(), _request({"timeout_seconds": 0}), _SELECTION)

    service._dispatch_single_attempt.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_keys_a_request_does_not_override_keep_their_configured_value():
    service = _service({"fulfillment_timeout_seconds": 300, "dispatch_timeout_seconds": 55})

    await service.execute_provisioning(MagicMock(), _request({"max_retries": 0}), _SELECTION)

    service._dispatch_single_attempt.assert_awaited_once()  # type: ignore[attr-defined]
    assert service._dispatch_single_attempt.await_args.args[4] == 55.0  # type: ignore[attr-defined]
