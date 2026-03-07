"""HostFactory plugin boundary contract tests (Boundary A).

Validates that every response ORB emits through the HF plugin interface
conforms to the schemas defined in tests/onaws/plugin_io_schemas.py.

The strictest contract is request_machines: additionalProperties=False means
any extra field added to that response immediately breaks HF.

These tests run without real AWS — the formatter methods are pure functions
that transform DTOs into dicts. No moto context is needed here.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

try:
    import jsonschema
except ImportError:
    pytest.skip("jsonschema not installed", allow_module_level=True)

from tests.onaws.plugin_io_schemas import (
    expected_get_available_templates_schema_hostfactory,
    expected_request_machines_schema_hostfactory,
    expected_request_status_schema_hostfactory,
)

from .conftest import make_machine_ref_dto, make_request_dto

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQ_ID_PATTERN = re.compile(r"^req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_STATUS_ID_PATTERN = re.compile(
    r"^(req-|ret-)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

HF_REQUEST_STATUSES = {
    "running",
    "complete",
    "complete_with_error",
    "failed",
    "partial",
    "cancelled",
    "timeout",
}
HF_MACHINE_RESULTS = {"executing", "succeed", "fail"}
HF_MACHINE_STATUSES = {"pending", "running", "terminated", "failed", "error"}


def _validate(instance: dict, schema: dict) -> None:
    """Raise AssertionError with a readable message on schema violation."""
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as exc:
        raise AssertionError(
            f"Schema validation failed:\n  path: {list(exc.absolute_path)}\n  message: {exc.message}\n  instance: {instance}"
        ) from exc


def _make_hf_template_dto(strategy):
    """Build a TemplateDTO that the HF strategy can format."""
    from orb.infrastructure.template.dtos import TemplateDTO

    return TemplateDTO(
        template_id="contract-tpl-hf",
        name="contract-tpl-hf",
        max_instances=4,
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        subnet_ids=["subnet-aabbccdd"],
        security_group_ids=["sg-11223344"],
        price_type="ondemand",
        provider_api="EC2Fleet",
    )


# ---------------------------------------------------------------------------
# 1. get_available_templates — HF response shape
# ---------------------------------------------------------------------------


def test_hf_get_templates_response_shape(hf_strategy):
    """format_templates_response output validates against HF get_available_templates schema."""
    template_dto = _make_hf_template_dto(hf_strategy)
    response = hf_strategy.format_templates_response([template_dto])

    _validate(response, expected_get_available_templates_schema_hostfactory)


def test_hf_get_templates_response_has_message(hf_strategy):
    """format_templates_response always includes a non-empty message string."""
    template_dto = _make_hf_template_dto(hf_strategy)
    response = hf_strategy.format_templates_response([template_dto])

    assert "message" in response
    assert isinstance(response["message"], str)


def test_hf_get_templates_attributes_shape(hf_strategy):
    """Each template in the HF response has the required attributes object."""
    template_dto = _make_hf_template_dto(hf_strategy)
    response = hf_strategy.format_templates_response([template_dto])

    assert len(response["templates"]) >= 1
    for tpl in response["templates"]:
        assert "attributes" in tpl, f"template missing 'attributes': {tpl}"
        attrs = tpl["attributes"]
        for key in ("type", "ncores", "ncpus", "nram"):
            assert key in attrs, f"attributes missing '{key}': {attrs}"
            assert isinstance(attrs[key], list) and len(attrs[key]) == 2


def test_hf_get_templates_empty_list(hf_strategy):
    """format_templates_response with empty list still validates against schema."""
    response = hf_strategy.format_templates_response([])
    _validate(response, expected_get_available_templates_schema_hostfactory)
    assert response["templates"] == []


# ---------------------------------------------------------------------------
# 2. request_machines — HF response shape (strictest: additionalProperties=False)
# ---------------------------------------------------------------------------


def test_hf_request_machines_response_shape(hf_strategy):
    """format_request_response output validates against HF request_machines schema.

    This is the most critical contract test: additionalProperties=False means
    any extra field added to this response immediately breaks HF.
    """
    request_data = {
        "request_id": "req-00000000-0000-0000-0000-000000000001",
        "status": "pending",
    }
    response = hf_strategy.format_request_response(request_data)

    _validate(response, expected_request_machines_schema_hostfactory)


def test_hf_request_machines_no_extra_fields(hf_strategy):
    """format_request_response must not emit any field beyond requestId and message.

    additionalProperties=False on the HF schema means this is a hard contract.
    """
    request_data = {
        "request_id": "req-00000000-0000-0000-0000-000000000002",
        "status": "pending",
    }
    response = hf_strategy.format_request_response(request_data)

    allowed = {"requestId", "message"}
    extra = set(response.keys()) - allowed
    assert not extra, f"Extra fields in request_machines response: {extra}"


def test_hf_request_machines_request_id_pattern(hf_strategy):
    """requestId in format_request_response matches req-<uuid4> pattern."""
    request_data = {
        "request_id": "req-aabbccdd-1122-3344-5566-778899aabbcc",
        "status": "pending",
    }
    response = hf_strategy.format_request_response(request_data)

    assert "requestId" in response
    assert _REQ_ID_PATTERN.match(response["requestId"]), (
        f"requestId '{response['requestId']}' does not match req-<uuid4> pattern"
    )


def test_hf_request_machines_all_statuses_produce_valid_response(hf_strategy):
    """format_request_response validates for every domain status value."""
    domain_statuses = [
        "pending",
        "in_progress",
        "complete",
        "failed",
        "cancelled",
        "timeout",
        "partial",
    ]
    for status in domain_statuses:
        request_data = {
            "request_id": "req-00000000-0000-0000-0000-000000000001",
            "status": status,
        }
        response = hf_strategy.format_request_response(request_data)
        (
            _validate(response, expected_request_machines_schema_hostfactory),
            (f"Schema validation failed for domain status '{status}'"),
        )


# ---------------------------------------------------------------------------
# 3. request_status — HF response shape
# ---------------------------------------------------------------------------


def test_hf_request_status_response_shape_no_machines(hf_strategy):
    """format_request_status_response with no machines validates against HF schema."""
    dto = make_request_dto(status="pending")
    response = hf_strategy.format_request_status_response([dto])

    _validate(response, expected_request_status_schema_hostfactory)


def test_hf_request_status_response_shape_with_machines(hf_strategy):
    """format_request_status_response with machines validates against HF schema."""
    machine = make_machine_ref_dto()
    dto = make_request_dto(status="complete", machine_refs=[machine])
    response = hf_strategy.format_request_status_response([dto])

    _validate(response, expected_request_status_schema_hostfactory)


def test_hf_request_status_request_id_pattern(hf_strategy):
    """requestId in status response matches (req-|ret-)<uuid4> pattern."""
    dto = make_request_dto(request_id="req-aabbccdd-1122-3344-5566-778899aabbcc")
    response = hf_strategy.format_request_status_response([dto])

    assert len(response["requests"]) == 1
    req_id = response["requests"][0]["requestId"]
    assert _STATUS_ID_PATTERN.match(req_id), (
        f"requestId '{req_id}' does not match (req-|ret-)<uuid4> pattern"
    )


def test_hf_request_status_status_enum(hf_strategy):
    """Every status value in the HF status response is in the allowed enum set."""
    for domain_status in [
        "pending",
        "in_progress",
        "complete",
        "failed",
        "cancelled",
        "timeout",
        "partial",
    ]:
        dto = make_request_dto(status=domain_status)
        response = hf_strategy.format_request_status_response([dto])
        emitted = response["requests"][0]["status"]
        assert emitted in HF_REQUEST_STATUSES, (
            f"domain status '{domain_status}' mapped to '{emitted}' which is not in HF allowed set {HF_REQUEST_STATUSES}"
        )


def test_hf_request_status_machine_result_enum(hf_strategy):
    """Machine result values in HF status response are in the allowed enum set."""
    for machine_status, expected_result in [
        ("running", "succeed"),
        ("pending", "executing"),
        ("terminated", "fail"),
        ("failed", "fail"),
        ("error", "fail"),
    ]:
        machine = make_machine_ref_dto(status=machine_status, result="executing")
        dto = make_request_dto(status="pending", machine_refs=[machine])
        response = hf_strategy.format_request_status_response([dto])

        machines = response["requests"][0]["machines"]
        assert len(machines) == 1
        result = machines[0]["result"]
        assert result in HF_MACHINE_RESULTS, (
            f"machine status '{machine_status}' produced result '{result}' not in {HF_MACHINE_RESULTS}"
        )


def test_hf_request_status_machine_fields_present(hf_strategy):
    """Each machine in HF status response has all required fields."""
    machine = make_machine_ref_dto()
    dto = make_request_dto(status="complete", machine_refs=[machine])
    response = hf_strategy.format_request_status_response([dto])

    required = {
        "machineId",
        "name",
        "result",
        "status",
        "privateIpAddress",
        "launchtime",
        "message",
    }
    machines = response["requests"][0]["machines"]
    assert len(machines) == 1
    missing = required - set(machines[0].keys())
    assert not missing, f"Machine missing required HF fields: {missing}"


def test_hf_request_status_machine_id_pattern(hf_strategy):
    """machineId in HF status response matches i-<hex> pattern."""
    machine = make_machine_ref_dto(machine_id="i-0abc1234def56789a")
    dto = make_request_dto(status="complete", machine_refs=[machine])
    response = hf_strategy.format_request_status_response([dto])

    machine_id = response["requests"][0]["machines"][0]["machineId"]
    assert re.match(r"^i-[0-9a-f]+$", machine_id), (
        f"machineId '{machine_id}' does not match i-<hex> pattern"
    )


def test_hf_request_status_multiple_requests(hf_strategy):
    """format_request_status_response with multiple DTOs validates against schema."""
    dtos = [
        make_request_dto(
            request_id="req-00000000-0000-0000-0000-000000000001",
            status="pending",
        ),
        make_request_dto(
            request_id="req-00000000-0000-0000-0000-000000000002",
            status="complete",
            machine_refs=[make_machine_ref_dto()],
        ),
    ]
    response = hf_strategy.format_request_status_response(dtos)
    _validate(response, expected_request_status_schema_hostfactory)
    assert len(response["requests"]) == 2


# ---------------------------------------------------------------------------
# 4. convert_domain_to_hostfactory_output — higher-level formatter
# ---------------------------------------------------------------------------


def test_hf_convert_domain_get_available_templates(hf_strategy):
    """convert_domain_to_hostfactory_output for getAvailableTemplates returns templates list.

    This path uses format_template_for_display (field mapper only) and does not
    inject the attributes block — that is done by format_templates_response.
    We validate the outer envelope shape only.
    """
    template_dto = _make_hf_template_dto(hf_strategy)
    response = hf_strategy.convert_domain_to_hostfactory_output(
        "getAvailableTemplates", [template_dto]
    )
    assert "templates" in response, f"Missing 'templates' key: {response}"
    assert isinstance(response["templates"], list)
    assert "message" in response, f"Missing 'message' key: {response}"


def test_hf_convert_domain_request_machines(hf_strategy):
    """convert_domain_to_hostfactory_output for requestMachines validates schema."""
    response = hf_strategy.convert_domain_to_hostfactory_output(
        "requestMachines",
        {"request_id": "req-00000000-0000-0000-0000-000000000001"},
    )
    _validate(response, expected_request_machines_schema_hostfactory)


def test_hf_convert_domain_get_request_status_dto(hf_strategy):
    """convert_domain_to_hostfactory_output for getRequestStatus with DTO validates schema."""
    machine = make_machine_ref_dto()
    dto = make_request_dto(status="complete", machine_refs=[machine])
    response = hf_strategy.convert_domain_to_hostfactory_output("getRequestStatus", dto)
    _validate(response, expected_request_status_schema_hostfactory)


def test_hf_convert_domain_get_request_status_dict(hf_strategy):
    """convert_domain_to_hostfactory_output for getRequestStatus with dict validates schema."""
    data = {
        "request_id": "req-00000000-0000-0000-0000-000000000001",
        "status": "pending",
        "machines": [],
    }
    response = hf_strategy.convert_domain_to_hostfactory_output("getRequestStatus", data)
    _validate(response, expected_request_status_schema_hostfactory)


# ---------------------------------------------------------------------------
# 5. Return request — requestId uses ret- prefix
# ---------------------------------------------------------------------------


def test_hf_return_request_id_uses_ret_prefix(hf_strategy):
    """Return request status response uses ret- prefix in requestId."""
    dto = make_request_dto(
        request_id="ret-00000000-0000-0000-0000-000000000001",
        status="pending",
        request_type="return",
    )
    response = hf_strategy.format_request_status_response([dto])
    req_id = response["requests"][0]["requestId"]
    assert req_id.startswith("ret-"), f"Return request ID should start with 'ret-', got: {req_id}"
    assert _STATUS_ID_PATTERN.match(req_id)
