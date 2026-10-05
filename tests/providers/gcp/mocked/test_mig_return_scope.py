"""MIG scope resolution for return and discovery flows that carry no placement."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.exceptions import GCPValidationError
from orb.providers.gcp.infrastructure.handlers.mig_handler import GCPManagedInstanceGroupHandler
from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy

_ZONAL = {"scope": "zonal", "zone": "us-central1-b"}
_REGIONAL = {"scope": "regional", "region": "us-east1"}


def _config() -> GCPProviderConfig:
    return GCPProviderConfig(
        project_id="orb-example-12345",
        region="us-central1",
        zones=["us-central1-a"],
    )


def _strategy_with_compute_client() -> tuple[GCPProviderStrategy, MagicMock]:
    strategy = GCPProviderStrategy(
        config=_config(), logger=MagicMock(), provider_name="gcp-default"
    )
    assert strategy.initialize() is True
    compute_client = MagicMock()
    handler = GCPManagedInstanceGroupHandler(
        compute_client=compute_client, config=_config(), logger=MagicMock()
    )
    strategy._handler_factory = SimpleNamespace(create_handler=lambda _api: handler)
    return strategy, compute_client


def _return_request(machine_ids: list[str]) -> Request:
    request = Request.create_return_request(
        machine_ids=machine_ids,
        provider_type="gcp",
        provider_name="gcp-default",
        provider_api="MIG",
    )
    assert request.provider_data == {}
    return request


def _coordinates(machine_id: str, placement: dict[str, str]) -> dict[str, dict]:
    return {
        machine_id: {
            "provider_api": "MIG",
            "resource_id": "mig-a",
            "provider_data": placement,
        }
    }


def _member(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        instance_url=f"projects/orb-example-12345/zones/us-central1-b/instances/{name}",
        instance_status="RUNNING",
        current_action="NONE",
    )


@pytest.mark.asyncio
async def test_return_request_terminate_uses_zonal_scope_from_machine() -> None:
    strategy, compute_client = _strategy_with_compute_client()
    compute_client.list_zonal_managed_instances.return_value = [_member("vm-a")]

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "request": _return_request(["vm-a"]),
                "instance_ids": ["vm-a"],
                "machine_coordinates": _coordinates("vm-a", _ZONAL),
            },
        )
    )

    assert result.success is True
    compute_client.delete_zonal_mig.assert_called_once()
    assert compute_client.delete_zonal_mig.call_args.kwargs["zone"] == "us-central1-b"
    compute_client.delete_regional_mig.assert_not_called()


@pytest.mark.asyncio
async def test_return_request_terminate_uses_regional_scope_from_machine() -> None:
    strategy, compute_client = _strategy_with_compute_client()
    compute_client.list_regional_managed_instances.return_value = [_member("vm-a")]

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "request": _return_request(["vm-a"]),
                "instance_ids": ["vm-a"],
                "machine_coordinates": _coordinates("vm-a", _REGIONAL),
            },
        )
    )

    assert result.success is True
    compute_client.delete_regional_mig.assert_called_once()
    assert compute_client.delete_regional_mig.call_args.kwargs["region"] == "us-east1"
    compute_client.delete_zonal_mig.assert_not_called()


@pytest.mark.asyncio
async def test_return_request_status_queries_zonal_mig_and_records_location() -> None:
    strategy, compute_client = _strategy_with_compute_client()
    compute_client.list_zonal_managed_instances.return_value = [_member("vm-a")]

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "request": _return_request(["vm-a"]),
                "instance_ids": ["vm-a"],
                "machine_coordinates": _coordinates("vm-a", _ZONAL),
            },
        )
    )

    assert result.success is True
    compute_client.list_regional_managed_instances.assert_not_called()
    assert compute_client.list_zonal_managed_instances.call_args.kwargs["zone"] == "us-central1-b"
    provider_data = result.data["instances"][0]["provider_data"]
    assert provider_data["scope"] == "zonal"
    assert provider_data["zone"] == "us-central1-b"


@pytest.mark.asyncio
async def test_return_request_status_queries_regional_mig() -> None:
    strategy, compute_client = _strategy_with_compute_client()
    compute_client.list_regional_managed_instances.return_value = [_member("vm-a")]

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "request": _return_request(["vm-a"]),
                "instance_ids": ["vm-a"],
                "machine_coordinates": _coordinates("vm-a", _REGIONAL),
            },
        )
    )

    assert result.success is True
    assert compute_client.list_regional_managed_instances.call_args.kwargs["region"] == "us-east1"
    assert result.data["instances"][0]["provider_data"]["region"] == "us-east1"


@pytest.mark.asyncio
async def test_acquire_request_discovery_uses_scope_from_request_provider_data() -> None:
    strategy, compute_client = _strategy_with_compute_client()
    compute_client.list_zonal_managed_instances.return_value = [_member("vm-a"), _member("vm-b")]
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="gcp-mig",
        machine_count=2,
        provider_type="gcp",
        provider_name="gcp-default",
    )
    request.provider_api = "MIG"
    request.provider_data = {**_ZONAL, "mig_name": "mig-a"}

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "request": request,
                "resource_ids": ["mig-a"],
                "provider_api": "MIG",
                "template_id": "gcp-mig",
            },
        )
    )

    assert result.success is True
    assert [i["instance_id"] for i in result.data["instances"]] == ["vm-a", "vm-b"]


@pytest.mark.asyncio
async def test_status_without_any_scope_source_fails_instead_of_guessing_regional() -> None:
    strategy, compute_client = _strategy_with_compute_client()

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "request": _return_request(["vm-a"]),
                "instance_ids": ["vm-a"],
                "machine_coordinates": _coordinates("vm-a", {}),
            },
        )
    )

    assert result.success is False
    assert "explicit scope" in (result.error_message or "")
    compute_client.list_regional_managed_instances.assert_not_called()
    compute_client.list_zonal_managed_instances.assert_not_called()


@pytest.mark.asyncio
async def test_terminate_rejects_machines_that_disagree_on_scope() -> None:
    strategy, compute_client = _strategy_with_compute_client()
    coordinates = {
        **_coordinates("vm-a", _ZONAL),
        **_coordinates("vm-b", _REGIONAL),
    }

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "request": _return_request(["vm-a", "vm-b"]),
                "instance_ids": ["vm-a", "vm-b"],
                "machine_coordinates": coordinates,
            },
        )
    )

    assert result.success is False
    compute_client.delete_zonal_mig.assert_not_called()
    compute_client.delete_regional_mig.assert_not_called()


def test_request_scope_wins_over_machine_coordinates() -> None:
    from orb.providers.gcp.services.operation_parameters import GCPMutationParameters

    request = _return_request(["vm-a"])
    request.provider_data = dict(_REGIONAL)

    params = GCPMutationParameters.from_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "request": request,
                "instance_ids": ["vm-a"],
                "machine_coordinates": _coordinates("vm-a", _ZONAL),
            },
        )
    )

    assert params.request_metadata.scope == "regional"
    assert params.request_metadata.zone is None


def test_conflicting_scope_raises_validation_error() -> None:
    from orb.providers.gcp.services.operation_parameters import GCPMutationParameters

    with pytest.raises(GCPValidationError, match="disagree"):
        GCPMutationParameters.from_operation(
            ProviderOperation(
                operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
                parameters={
                    "provider_api": "MIG",
                    "instance_ids": ["vm-a", "vm-b"],
                    "machine_coordinates": {
                        **_coordinates("vm-a", _ZONAL),
                        **_coordinates("vm-b", _REGIONAL),
                    },
                },
            )
        )
