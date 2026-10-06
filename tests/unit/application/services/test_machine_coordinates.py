"""Tests for resolving persisted machine coordinates."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.services.machine_coordinates import load_machine_coordinates

_ORIGIN_ID = "req-12345678-1234-1234-1234-123456789abc"


def _machine(machine_id: str, provider_data: dict, request_id: str | None = _ORIGIN_ID):
    machine = MagicMock()
    machine.machine_id.value = machine_id
    machine.provider_api = "MIG"
    machine.resource_id = "mig-a"
    machine.provider_data = provider_data
    machine.request_id = request_id
    return machine


def _factory(machines: list, origin_provider_data: dict | None):
    uow = MagicMock()
    uow.__enter__.return_value = uow
    uow.machines.find_by_ids.return_value = machines
    origin = (
        MagicMock(provider_data=origin_provider_data) if origin_provider_data is not None else None
    )
    uow.requests.get_by_id.return_value = origin
    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    return factory, uow


@pytest.mark.unit
def test_machine_provider_data_is_returned_with_resource() -> None:
    factory, _ = _factory([_machine("vm-a", {"scope": "zonal", "zone": "z1"})], {})

    result = load_machine_coordinates(factory, ["vm-a"])

    assert result["vm-a"] == {
        "provider_api": "MIG",
        "resource_id": "mig-a",
        "provider_data": {"scope": "zonal", "zone": "z1"},
    }


@pytest.mark.unit
def test_missing_placement_is_filled_from_origin_request() -> None:
    factory, uow = _factory(
        [_machine("vm-a", {"cloud_host_id": "vm-a"})],
        {"scope": "regional", "region": "us-east1"},
    )

    result = load_machine_coordinates(factory, ["vm-a"])

    assert result["vm-a"]["provider_data"] == {
        "cloud_host_id": "vm-a",
        "scope": "regional",
        "region": "us-east1",
    }
    uow.requests.get_by_id.assert_called_once()


@pytest.mark.unit
def test_machine_values_win_over_origin_request() -> None:
    factory, _ = _factory(
        [_machine("vm-a", {"scope": "zonal", "zone": "z1"})],
        {"scope": "regional", "region": "us-east1"},
    )

    result = load_machine_coordinates(factory, ["vm-a"])

    assert result["vm-a"]["provider_data"]["scope"] == "zonal"


@pytest.mark.unit
def test_no_machine_ids_skips_storage() -> None:
    factory, _ = _factory([], None)

    assert load_machine_coordinates(factory, []) == {}
    factory.create_unit_of_work.assert_not_called()
