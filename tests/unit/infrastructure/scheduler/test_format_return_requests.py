"""Unit tests for HostFactorySchedulerStrategy.format_return_requests_response.

Covers Fix 1 (getReturnRequests envelope shape), Fix 3 (mandatory fail message),
Fix 4 (no ncores in attributes), and Fix 5 (no region in machine details).
"""

import pytest

from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)


@pytest.fixture
def hf():
    return HostFactorySchedulerStrategy()


# ---------------------------------------------------------------------------
# Fix 1: format_return_requests_response — correct envelope shape
# ---------------------------------------------------------------------------


def test_empty_input_returns_no_machines_message(hf):
    result = hf.format_return_requests_response([])
    assert result == {
        "status": "complete",
        "message": "No machines to return.",
        "requests": [],
    }


def test_single_request_single_machine_with_hostname(hf):
    requests = [
        {
            "request_id": "ret-001",
            "grace_period": 300,
            "machines": [{"name": "host-1", "machine_id": "i-abc"}],
        }
    ]
    result = hf.format_return_requests_response(requests)
    assert result["status"] == "complete"
    assert result["requests"] == [{"machine": "host-1", "gracePeriod": 300}]


def test_single_request_multiple_machines_all_get_same_grace_period(hf):
    requests = [
        {
            "request_id": "ret-002",
            "grace_period": 120,
            "machines": [
                {"name": "host-1", "machine_id": "i-001"},
                {"name": "host-2", "machine_id": "i-002"},
                {"name": "host-3", "machine_id": "i-003"},
            ],
        }
    ]
    result = hf.format_return_requests_response(requests)
    assert len(result["requests"]) == 3
    for item in result["requests"]:
        assert item["gracePeriod"] == 120


def test_multiple_requests_each_machine_gets_its_request_grace_period(hf):
    requests = [
        {
            "request_id": "ret-003",
            "grace_period": 60,
            "machines": [{"name": "host-a", "machine_id": "i-aaa"}],
        },
        {
            "request_id": "ret-004",
            "grace_period": 600,
            "machines": [{"name": "host-b", "machine_id": "i-bbb"}],
        },
    ]
    result = hf.format_return_requests_response(requests)
    items = result["requests"]
    assert len(items) == 2
    host_a = next(i for i in items if i["machine"] == "host-a")
    host_b = next(i for i in items if i["machine"] == "host-b")
    assert host_a["gracePeriod"] == 60
    assert host_b["gracePeriod"] == 600


def test_spot_priced_grace_period_passthrough(hf):
    requests = [
        {
            "grace_period": 120,
            "machines": [{"name": "spot-host", "machine_id": "i-spot"}],
        }
    ]
    result = hf.format_return_requests_response(requests)
    assert result["requests"][0]["gracePeriod"] == 120


def test_machine_with_no_hostname_falls_back_to_machine_id(hf):
    requests = [
        {
            "grace_period": 300,
            "machines": [{"machine_id": "i-fallback"}],
        }
    ]
    result = hf.format_return_requests_response(requests)
    assert result["requests"] == [{"machine": "i-fallback", "gracePeriod": 300}]


def test_machine_with_neither_hostname_nor_machine_id_is_skipped(hf):
    requests = [
        {
            "grace_period": 300,
            "machines": [{"status": "running"}],
        }
    ]
    result = hf.format_return_requests_response(requests)
    assert result["requests"] == []


def test_input_as_plain_dicts(hf):
    requests = [{"grace_period": 90, "machines": [{"name": "dict-host", "machine_id": "i-d"}]}]
    result = hf.format_return_requests_response(requests)
    assert result["requests"] == [{"machine": "dict-host", "gracePeriod": 90}]


def test_items_have_exactly_machine_and_grace_period_keys(hf):
    requests = [
        {
            "request_id": "ret-x",
            "status": "complete",
            "grace_period": 300,
            "machines": [{"name": "host-x", "machine_id": "i-x", "status": "running"}],
        }
    ]
    result = hf.format_return_requests_response(requests)
    assert len(result["requests"]) == 1
    item = result["requests"][0]
    assert set(item.keys()) == {"machine", "gracePeriod"}


def test_grace_period_is_int_not_str_or_float(hf):
    requests = [
        {
            "grace_period": 300,
            "machines": [{"name": "host-1", "machine_id": "i-1"}],
        }
    ]
    result = hf.format_return_requests_response(requests)
    gp = result["requests"][0]["gracePeriod"]
    assert isinstance(gp, int), f"gracePeriod should be int, got {type(gp)}"


def test_non_empty_message_when_items_present(hf):
    requests = [
        {
            "grace_period": 300,
            "machines": [{"name": "host-1", "machine_id": "i-1"}],
        }
    ]
    result = hf.format_return_requests_response(requests)
    assert result["message"] == "Return requests retrieved successfully."


def test_machine_references_key_also_accepted(hf):
    """machine_references is the DTO field name; formatter must handle both keys."""
    requests = [
        {
            "grace_period": 200,
            "machine_references": [{"name": "ref-host", "machine_id": "i-ref"}],
        }
    ]
    result = hf.format_return_requests_response(requests)
    assert result["requests"] == [{"machine": "ref-host", "gracePeriod": 200}]


# ---------------------------------------------------------------------------
# Fix 2 & 4: _create_hf_attributes and _build_hf_attributes — no ncores, strings
# ---------------------------------------------------------------------------


def test_create_hf_attributes_no_ncores(hf):
    attrs = hf.field_mapper._create_hf_attributes("t3.micro")
    assert "ncores" not in attrs, "ncores is LSF-only and must not appear in HF attributes"


def test_create_hf_attributes_ncpus_is_string(hf):
    attrs = hf.field_mapper._create_hf_attributes("t3.micro")
    assert isinstance(attrs["ncpus"][1], str), (
        f"ncpus value should be str, got {type(attrs['ncpus'][1])}"
    )


def test_create_hf_attributes_nram_is_string(hf):
    attrs = hf.field_mapper._create_hf_attributes("t3.micro")
    assert isinstance(attrs["nram"][1], str), (
        f"nram value should be str, got {type(attrs['nram'][1])}"
    )


def test_create_hf_attributes_pattern(hf):
    attrs = hf.field_mapper._create_hf_attributes("m5.xlarge")
    assert attrs["ncpus"] == ["Numeric", str(attrs["ncpus"][1])]
    assert attrs["nram"] == ["Numeric", str(attrs["nram"][1])]


def test_build_hf_attributes_no_ncores(hf):
    attrs = hf._build_hf_attributes("t3.micro")
    assert "ncores" not in attrs, "ncores is LSF-only and must not appear in _build_hf_attributes"


def test_build_hf_attributes_ncpus_is_string(hf):
    attrs = hf._build_hf_attributes("t3.micro")
    assert isinstance(attrs["ncpus"][1], str)


def test_build_hf_attributes_nram_is_string(hf):
    attrs = hf._build_hf_attributes("t3.micro")
    assert isinstance(attrs["nram"][1], str)


# ---------------------------------------------------------------------------
# Fix 3: mandatory non-empty message on fail
# ---------------------------------------------------------------------------


def test_fail_result_with_no_detail_gets_default_message(hf):
    machines = [{"machine_id": "i-fail", "status": "failed"}]
    formatted = hf._format_machines_for_hostfactory(machines, request_type="provision")
    assert formatted[0]["result"] == "fail"
    assert formatted[0]["message"], "message must be non-empty when result==fail"
    assert formatted[0]["message"] == "Machine failed (no detail available)"


def test_fail_result_with_status_reason_uses_it(hf):
    machines = [
        {"machine_id": "i-fail", "status": "failed", "status_reason": "InsufficientCapacity"}
    ]
    formatted = hf._format_machines_for_hostfactory(machines, request_type="provision")
    assert formatted[0]["message"] == "InsufficientCapacity"


def test_fail_result_with_error_field_uses_it(hf):
    machines = [{"machine_id": "i-fail", "status": "failed", "error": "Spot interrupted"}]
    formatted = hf._format_machines_for_hostfactory(machines, request_type="provision")
    assert formatted[0]["message"] == "Spot interrupted"


def test_fail_result_with_message_field_uses_it(hf):
    machines = [{"machine_id": "i-fail", "status": "failed", "message": "Custom error"}]
    formatted = hf._format_machines_for_hostfactory(machines, request_type="provision")
    assert formatted[0]["message"] == "Custom error"


# ---------------------------------------------------------------------------
# Fix 5: no region in format_machine_details_response
# ---------------------------------------------------------------------------


def test_format_machine_details_no_region_key(hf):
    machine_data = {
        "name": "host-1",
        "status": "running",
        "machine_id": "i-abc",
        "region": "eu-west-1",
    }
    result = hf.format_machine_details_response(machine_data)
    assert "region" not in result, f"region must not appear in HF machine details, got: {result}"


def test_format_machine_details_required_fields_present(hf):
    machine_data = {
        "name": "host-1",
        "status": "running",
        "machine_id": "i-abc",
        "provider_type": "aws",
    }
    result = hf.format_machine_details_response(machine_data)
    assert "name" in result
    assert "status" in result
    assert "machineId" in result
    assert "provider" in result
