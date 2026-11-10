"""One machine shape for every HostFactory operation that lists machines."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
    _epoch_seconds,
)


@pytest.fixture
def hf() -> HostFactorySchedulerStrategy:
    return HostFactorySchedulerStrategy()


def _machine(**overrides: Any) -> dict[str, Any]:
    machine = {
        "machine_id": "i-0abc",
        "name": "host-1",
        "status": "running",
        "private_ip": "10.0.0.5",
        "launch_time": 1734619942,
        "instance_type": "m5.large",
    }
    machine.update(overrides)
    return machine


def _request(**overrides: Any) -> dict[str, Any]:
    request = {
        "request_id": "req-1",
        "status": "complete",
        "request_type": "acquire",
        "machines": [_machine()],
    }
    request.update(overrides)
    return request


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_request_item_extensions_are_camel_case(hf):
    request = _request(
        resource_ids=["fleet-1"],
        provider_name="aws-1",
        provider_type="aws",
        provider_api="EC2Fleet",
        target_units=4,
        fulfilled_units=2,
        running_count=2,
        pending_count=0,
    )

    body = hf.format_request_status_response([request])

    assert not {k for k in _keys(body) if "_" in k}
    item = body["requests"][0]
    assert item["resourceIds"] == ["fleet-1"]
    assert (item["targetUnits"], item["fulfilledUnits"]) == (4, 2)
    assert (item["runningCount"], item["pendingCount"]) == (2, 0)


def test_machines_list_uses_the_request_item_machine_shape(hf):
    from_request = hf.format_request_status_response([_request(request_type=None)])
    listed = hf.format_machine_status_response([_machine()])

    assert listed["machines"][0] == from_request["requests"][0]["machines"][0]


def test_machine_detail_uses_the_same_shape(hf):
    assert (
        hf.format_machine_details_response(_machine())
        == hf.format_machine_status_response([_machine()])["machines"][0]
    )


@pytest.mark.parametrize(
    "launch_time",
    [1734619942, 1734619942.9, "1734619942", "2024-12-19T14:52:22+00:00", "2024-12-19T14:52:22Z"],
)
def test_launchtime_is_integer_epoch_seconds_on_every_path(hf, launch_time):
    expected = 1734619942
    machine = _machine(launch_time=launch_time)

    on_status = hf.format_request_status_response([_request(machines=[machine])])
    on_list = hf.format_machine_status_response([machine])

    assert on_status["requests"][0]["machines"][0]["launchtime"] == expected
    assert on_list["machines"][0]["launchtime"] == expected
    assert isinstance(on_list["machines"][0]["launchtime"], int)


def test_launchtime_accepts_datetime_and_defaults_to_zero():
    assert _epoch_seconds(datetime(2024, 12, 19, 14, 52, 22, tzinfo=timezone.utc)) == 1734619942
    assert _epoch_seconds(None) == 0
    assert _epoch_seconds("not a time") == 0


def test_unknown_private_ip_is_an_empty_string(hf):
    for ip in (None, ""):
        machine = hf.format_machine_status_response([_machine(private_ip=ip)])["machines"][0]
        assert machine["privateIpAddress"] == ""


def test_failed_machine_carries_a_non_empty_message(hf):
    machine = hf.format_machine_details_response(_machine(status="failed"))

    assert machine["result"] == "fail"
    assert machine["message"]


def test_pending_machine_keeps_its_raw_status(hf):
    machine = hf.format_machine_details_response(_machine(status="pending"))

    assert machine["result"] == "executing"
    assert machine["status"] == "pending"


def test_instance_tags_are_semicolon_separated_and_not_escaped(hf):
    machine = hf.format_machine_details_response(_machine(tags={"b": "2", "a": "x;y=z"}))

    # A value containing ';' or '=' is not escaped: a splitting consumer misreads it.
    assert machine["instanceTags"] == "a=x;y=z;b=2"
    assert hf.format_machine_details_response(_machine())["instanceTags"] == ""


def test_failed_request_message_carries_the_diagnostic(hf):
    diagnostic = {"category": "capacity", "provider_errors": []}

    body = hf.format_request_status_response(
        [
            _request(
                status="failed",
                message="",
                fulfilment_diagnostic=diagnostic,
                fulfilled_units=1,
                target_units=3,
            )
        ]
    )

    assert body["requests"][0]["message"] == (
        "Partially fulfilled: 1/3 (insufficient provider capacity)"
    )


def test_request_message_is_unchanged_without_a_diagnostic(hf):
    body = hf.format_request_status_response([_request(message="done")])

    assert body["requests"][0]["message"] == "done"
