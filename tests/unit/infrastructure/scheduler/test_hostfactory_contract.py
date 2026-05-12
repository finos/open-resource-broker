"""HF contract tests — pin the exact IBM HostFactory spec output shapes.

These tests must not be weakened. They document the IBM HF spec requirements:
- Only 3 allowed status values: running, complete, complete_with_error
- camelCase field names (requestId, not request_id)
- Machine result field: executing, fail, succeed
- Template attributes object with required keys
"""

from datetime import datetime, timezone

import pytest

from orb.application.request.dto import RequestDTO
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.dtos import TemplateDTO

DOMAIN_STATUSES = [
    "pending",
    "in_progress",
    "acquiring",
    "provisioning",
    "complete",
    "completed",
    "partial",
    "failed",
    "cancelled",
    "timeout",
    "error",
]

HF_ALLOWED_STATUSES = {"running", "complete", "complete_with_error"}

MACHINE_STATUSES_ACQUIRE = ["running", "pending", "launching", "terminated", "failed", "error"]
MACHINE_RESULT_ALLOWED = {"executing", "fail", "succeed"}


@pytest.fixture
def hf_strategy():
    s = HostFactorySchedulerStrategy()
    s._config_manager = None
    s._logger = None
    return s


def _make_dto(status: str) -> RequestDTO:
    return RequestDTO(
        request_id="req-abc",
        status=status,
        requested_count=1,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# 2a. Status vocabulary — only 3 values allowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("domain_status", DOMAIN_STATUSES)
def test_hf_status_vocabulary(hf_strategy, domain_status):
    """Every domain status must map to one of the 3 HF-allowed values."""
    result = hf_strategy.format_request_status_response([_make_dto(domain_status)])
    hf_status = result["requests"][0]["status"]
    assert hf_status in HF_ALLOWED_STATUSES, (
        f"domain status '{domain_status}' mapped to '{hf_status}', not in {HF_ALLOWED_STATUSES}"
    )


# ---------------------------------------------------------------------------
# 2b. camelCase field contract on request status response
# ---------------------------------------------------------------------------


def test_hf_request_status_response_fields(hf_strategy):
    """format_request_status_response must use camelCase keys per HF spec."""
    result = hf_strategy.format_request_status_response([_make_dto("complete")])
    req = result["requests"][0]
    assert "requestId" in req, "HF spec requires 'requestId' (camelCase)"
    assert "request_id" not in req, "HF spec forbids snake_case 'request_id'"
    assert "status" in req
    assert "machines" in req


def test_hf_request_status_response_has_requests_key(hf_strategy):
    """format_request_status_response must return a dict with 'requests' list."""
    result = hf_strategy.format_request_status_response([_make_dto("pending")])
    assert "requests" in result
    assert isinstance(result["requests"], list)


def test_hf_request_status_response_multiple_dtos(hf_strategy):
    """Multiple DTOs must each appear as a separate entry in requests list."""
    dtos = [_make_dto("complete"), _make_dto("pending"), _make_dto("failed")]
    result = hf_strategy.format_request_status_response(dtos)
    assert len(result["requests"]) == 3
    for req in result["requests"]:
        assert "requestId" in req
        assert req["status"] in HF_ALLOWED_STATUSES


# ---------------------------------------------------------------------------
# 2c. Machine result field — 3 values only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("machine_status", MACHINE_STATUSES_ACQUIRE)
def test_hf_machine_result_field(hf_strategy, machine_status):
    """Machine result field must be one of executing/fail/succeed."""
    machines = [{"machine_id": "i-1", "status": machine_status}]
    formatted = hf_strategy._format_machines_for_hostfactory(machines, request_type="provision")
    assert formatted[0]["result"] in MACHINE_RESULT_ALLOWED, (
        f"machine status '{machine_status}' produced result '{formatted[0]['result']}', "
        f"not in {MACHINE_RESULT_ALLOWED}"
    )


def test_hf_machine_result_return_request_terminated(hf_strategy):
    """For return requests, terminated machines must map to 'succeed'."""
    machines = [{"machine_id": "i-1", "status": "terminated"}]
    formatted = hf_strategy._format_machines_for_hostfactory(machines, request_type="return")
    assert formatted[0]["result"] == "succeed"


def test_hf_machine_result_return_request_running(hf_strategy):
    """For return requests, running machines must map to 'executing' (termination in flight)."""
    machines = [{"machine_id": "i-1", "status": "running"}]
    formatted = hf_strategy._format_machines_for_hostfactory(machines, request_type="return")
    assert formatted[0]["result"] == "executing"


def test_hf_machine_result_acquire_running(hf_strategy):
    """For acquire requests, running machines must map to 'succeed'."""
    machines = [{"machine_id": "i-1", "status": "running"}]
    formatted = hf_strategy._format_machines_for_hostfactory(machines, request_type="provision")
    assert formatted[0]["result"] == "succeed"


# ---------------------------------------------------------------------------
# 2d. getAvailableTemplates — required fields
# ---------------------------------------------------------------------------


def test_hf_templates_response_required_fields(hf_strategy):
    """Every template in HF response must have templateId, maxNumber, attributes."""
    t = TemplateDTO(
        template_id="tpl-1",
        name="T1",
        image_id="ami-abc",
        max_instances=5,
        subnet_ids=["subnet-1"],
    )
    result = hf_strategy.format_templates_response([t])
    assert "templates" in result
    tpl = result["templates"][0]
    assert "templateId" in tpl, "HF spec requires 'templateId'"
    assert "maxNumber" in tpl, "HF spec requires 'maxNumber'"
    assert "attributes" in tpl, "HF spec requires 'attributes' object"
    attrs = tpl["attributes"]
    assert "type" in attrs
    assert "ncpus" in attrs
    assert "nram" in attrs


def test_hf_templates_response_message_present(hf_strategy):
    """HF templates response must include a message field."""
    t = TemplateDTO(
        template_id="tpl-1",
        name="T1",
        image_id="ami-abc",
        max_instances=1,
        subnet_ids=["subnet-1"],
    )
    result = hf_strategy.format_templates_response([t])
    assert "message" in result
    assert isinstance(result["message"], str)


# ---------------------------------------------------------------------------
# 2e. format_machine_status_response — cloudHostId must be present
# ---------------------------------------------------------------------------


def _make_machine_dto(**overrides):
    """Build a minimal MachineDTO for formatter tests."""
    from orb.application.dto.responses import MachineDTO

    defaults = {
        "machine_id": "i-abc123",
        "name": "test-machine",
        "status": "running",
        "instance_type": "t3.medium",
        "private_ip": "10.0.0.1",
        "result": "succeed",
    }
    defaults.update(overrides)
    return MachineDTO(**defaults)


def test_format_machine_status_response_includes_cloud_host_id(hf_strategy):
    """format_machine_status_response must emit cloudHostId per IBM HF spec."""
    dto = _make_machine_dto(cloud_host_id="aws-host-xyz")
    result = hf_strategy.format_machine_status_response([dto])
    machine = result["machines"][0]
    assert "cloudHostId" in machine, "HF spec requires 'cloudHostId' in machine response"
    assert machine["cloudHostId"] == "aws-host-xyz"


def test_format_machine_status_response_cloud_host_id_none_when_not_set(hf_strategy):
    """cloudHostId must be present (as None) even when not populated on the DTO."""
    dto = _make_machine_dto()
    result = hf_strategy.format_machine_status_response([dto])
    machine = result["machines"][0]
    assert "cloudHostId" in machine, "cloudHostId key must always be present"
    assert machine["cloudHostId"] is None


def test_format_machine_status_response_has_machines_key(hf_strategy):
    """format_machine_status_response must return a dict with a 'machines' list."""
    result = hf_strategy.format_machine_status_response([_make_machine_dto()])
    assert "machines" in result
    assert isinstance(result["machines"], list)


def test_format_machine_status_response_core_fields_present(hf_strategy):
    """Core camelCase fields must be present in each machine entry."""
    dto = _make_machine_dto(
        template_id="tmpl-1",
        request_id="req-1",
        image_id="ami-abc",
        subnet_id="subnet-1",
    )
    result = hf_strategy.format_machine_status_response([dto])
    machine = result["machines"][0]
    for field in (
        "machineId",
        "templateId",
        "requestId",
        "vmType",
        "imageId",
        "privateIp",
        "status",
        "cloudHostId",
    ):
        assert field in machine, f"Missing required field: {field}"


def test_hf_templates_response_empty_list(hf_strategy):
    """Empty template list must still return valid HF structure."""
    result = hf_strategy.format_templates_response([])
    assert "templates" in result
    assert result["templates"] == []
    assert "message" in result


# ---------------------------------------------------------------------------
# 2e. requestMachines response — camelCase requestId
# ---------------------------------------------------------------------------


def test_hf_request_response_camel_case(hf_strategy):
    """format_request_response must return requestId (camelCase) for HF."""
    result = hf_strategy.format_request_response({"request_id": "req-xyz", "status": "pending"})
    assert "requestId" in result, "HF spec requires 'requestId' (camelCase)"
    assert "request_id" not in result, "HF spec forbids snake_case 'request_id'"


def test_hf_request_response_complete_status(hf_strategy):
    """format_request_response for complete status must include message."""
    result = hf_strategy.format_request_response({"request_id": "req-xyz", "status": "complete"})
    assert "requestId" in result
    assert "message" in result


def test_hf_request_response_failed_status(hf_strategy):
    """format_request_response for failed status must include requestId and message."""
    result = hf_strategy.format_request_response({"request_id": "req-xyz", "status": "failed"})
    assert "requestId" in result
    assert "message" in result


@pytest.mark.parametrize(
    "status", ["pending", "in_progress", "complete", "failed", "cancelled", "timeout", "partial"]
)
def test_hf_request_response_always_camel_case(hf_strategy, status):
    """format_request_response must always use camelCase regardless of status."""
    result = hf_strategy.format_request_response({"request_id": "req-1", "status": status})
    assert "requestId" in result
    assert "request_id" not in result


# ---------------------------------------------------------------------------
# 2f. Status mapping correctness
# ---------------------------------------------------------------------------


def test_hf_pending_maps_to_running(hf_strategy):
    """pending domain status must map to 'running' HF status."""
    result = hf_strategy.format_request_status_response([_make_dto("pending")])
    assert result["requests"][0]["status"] == "running"


def test_hf_in_progress_maps_to_running(hf_strategy):
    """in_progress domain status must map to 'running' HF status."""
    result = hf_strategy.format_request_status_response([_make_dto("in_progress")])
    assert result["requests"][0]["status"] == "running"


def test_hf_complete_maps_to_complete(hf_strategy):
    """complete domain status must map to 'complete' HF status."""
    result = hf_strategy.format_request_status_response([_make_dto("complete")])
    assert result["requests"][0]["status"] == "complete"


def test_hf_failed_maps_to_complete_with_error(hf_strategy):
    """failed domain status must map to 'complete_with_error' HF status."""
    result = hf_strategy.format_request_status_response([_make_dto("failed")])
    assert result["requests"][0]["status"] == "complete_with_error"


def test_hf_partial_maps_to_complete_with_error(hf_strategy):
    """partial domain status must map to 'complete_with_error' HF status."""
    result = hf_strategy.format_request_status_response([_make_dto("partial")])
    assert result["requests"][0]["status"] == "complete_with_error"
