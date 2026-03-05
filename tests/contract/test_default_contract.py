"""Default scheduler boundary contract tests.

Validates that every response ORB emits through the default scheduler
interface conforms to the schemas defined in tests/onaws/plugin_io_schemas.py.
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
    expected_get_available_templates_schema_default,
    expected_request_machines_schema_default,
    expected_request_status_schema_default,
)

from .conftest import make_machine_ref_dto, make_request_dto

_REQ_ID_PATTERN = re.compile(r"^req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_STATUS_ID_PATTERN = re.compile(
    r"^(req-|ret-)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _validate(instance: dict, schema: dict) -> None:
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as exc:
        raise AssertionError(
            f"Schema validation failed:\n  path: {list(exc.absolute_path)}\n"
            f"  message: {exc.message}\n  instance: {instance}"
        ) from exc


def _make_default_template_dto():
    from infrastructure.template.dtos import TemplateDTO

    return TemplateDTO(
        template_id="contract-tpl-default",
        name="contract-tpl-default",
        max_instances=4,
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        subnet_ids=["subnet-aabbccdd"],
        security_group_ids=["sg-11223344"],
        price_type="ondemand",
        provider_api="EC2Fleet",
    )


# ---------------------------------------------------------------------------
# 1. get_available_templates — default response shape
# ---------------------------------------------------------------------------


def test_default_get_templates_response_shape(default_strategy):
    """format_templates_response output validates against default get_available_templates schema."""
    template_dto = _make_default_template_dto()
    response = default_strategy.format_templates_response([template_dto])
    _validate(response, expected_get_available_templates_schema_default)


def test_default_get_templates_required_fields(default_strategy):
    """Each template in default response has template_id, max_capacity, instance_type."""
    template_dto = _make_default_template_dto()
    response = default_strategy.format_templates_response([template_dto])

    assert len(response["templates"]) >= 1
    for tpl in response["templates"]:
        assert "template_id" in tpl, f"template missing 'template_id': {tpl}"
        assert "max_capacity" in tpl, f"template missing 'max_capacity': {tpl}"
        assert "instance_type" in tpl, f"template missing 'instance_type': {tpl}"


def test_default_get_templates_total_count(default_strategy):
    """format_templates_response includes total_count matching len(templates)."""
    dtos = [_make_default_template_dto(), _make_default_template_dto()]
    # Give them distinct IDs
    dtos[1] = dtos[1].model_copy(update={"template_id": "contract-tpl-default-2"})
    response = default_strategy.format_templates_response(dtos)
    assert response["total_count"] == len(response["templates"])


def test_default_get_templates_empty_list(default_strategy):
    """format_templates_response with empty list validates against schema."""
    response = default_strategy.format_templates_response([])
    _validate(response, expected_get_available_templates_schema_default)
    assert response["templates"] == []
    assert response["total_count"] == 0


# ---------------------------------------------------------------------------
# 2. request_machines — default response shape
# ---------------------------------------------------------------------------


def test_default_request_machines_response_shape(default_strategy):
    """format_request_response output validates against default request_machines schema."""
    request_data = {
        "request_id": "req-00000000-0000-0000-0000-000000000001",
        "status": "pending",
    }
    response = default_strategy.format_request_response(request_data)
    _validate(response, expected_request_machines_schema_default)


def test_default_request_machines_request_id_pattern(default_strategy):
    """request_id in default format_request_response matches req-<uuid4> pattern."""
    request_data = {
        "request_id": "req-aabbccdd-1122-3344-5566-778899aabbcc",
        "status": "pending",
    }
    response = default_strategy.format_request_response(request_data)

    assert "request_id" in response
    assert _REQ_ID_PATTERN.match(response["request_id"]), (
        f"request_id '{response['request_id']}' does not match req-<uuid4> pattern"
    )


def test_default_request_machines_uses_snake_case_key(default_strategy):
    """Default scheduler uses request_id (snake_case), not requestId (camelCase)."""
    request_data = {
        "request_id": "req-00000000-0000-0000-0000-000000000001",
        "status": "pending",
    }
    response = default_strategy.format_request_response(request_data)

    assert "request_id" in response, "Default scheduler must use 'request_id' (snake_case)"
    assert "requestId" not in response, "Default scheduler must NOT use 'requestId' (camelCase)"


# ---------------------------------------------------------------------------
# 3. request_status — default response shape
# ---------------------------------------------------------------------------


def test_default_request_status_response_shape_no_machines(default_strategy):
    """format_request_status_response with no machines validates against default schema."""
    dto = make_request_dto(status="pending")
    response = default_strategy.format_request_status_response([dto])
    _validate(response, expected_request_status_schema_default)


def test_default_request_status_response_shape_with_machines(default_strategy):
    """format_request_status_response with machines validates against default schema."""
    machine = make_machine_ref_dto()
    dto = make_request_dto(status="complete", machine_refs=[machine])
    response = default_strategy.format_request_status_response([dto])
    _validate(response, expected_request_status_schema_default)


def test_default_request_status_uses_snake_case_keys(default_strategy):
    """Default scheduler status response uses snake_case keys (request_id, machine_id, etc.)."""
    machine = make_machine_ref_dto()
    dto = make_request_dto(status="complete", machine_refs=[machine])
    response = default_strategy.format_request_status_response([dto])

    req = response["requests"][0]
    assert "request_id" in req, f"Expected 'request_id' in request, got keys: {list(req.keys())}"
    assert "requestId" not in req, "Default scheduler must NOT use 'requestId'"

    if req["machines"]:
        machine_out = req["machines"][0]
        assert "machine_id" in machine_out, (
            f"Expected 'machine_id' in machine, got keys: {list(machine_out.keys())}"
        )
        assert "machineId" not in machine_out, "Default scheduler must NOT use 'machineId'"


def test_default_request_status_request_id_pattern(default_strategy):
    """request_id in default status response matches (req-|ret-)<uuid4> pattern."""
    dto = make_request_dto(request_id="req-aabbccdd-1122-3344-5566-778899aabbcc")
    response = default_strategy.format_request_status_response([dto])

    req_id = response["requests"][0]["request_id"]
    assert _STATUS_ID_PATTERN.match(req_id), (
        f"request_id '{req_id}' does not match (req-|ret-)<uuid4> pattern"
    )


def test_default_request_status_machine_fields_present(default_strategy):
    """Each machine in default status response has all required fields."""
    machine = make_machine_ref_dto()
    dto = make_request_dto(status="complete", machine_refs=[machine])
    response = default_strategy.format_request_status_response([dto])

    required = {
        "machine_id",
        "name",
        "result",
        "status",
        "private_ip_address",
        "launch_time",
        "message",
    }
    machines = response["requests"][0]["machines"]
    assert len(machines) == 1
    missing = required - set(machines[0].keys())
    assert not missing, f"Machine missing required default fields: {missing}"


# ---------------------------------------------------------------------------
# 4. Scheduler type identifier
# ---------------------------------------------------------------------------


def test_default_scheduler_type_identifier(default_strategy):
    """DefaultSchedulerStrategy.get_scheduler_type() returns 'default'."""
    assert default_strategy.get_scheduler_type() == "default"


def test_hf_scheduler_type_identifier(hf_strategy):
    """HostFactorySchedulerStrategy.get_scheduler_type() returns 'hostfactory'."""
    assert hf_strategy.get_scheduler_type() == "hostfactory"


# ---------------------------------------------------------------------------
# 5. Key divergence: HF vs default use different key names
# ---------------------------------------------------------------------------


def test_hf_and_default_use_different_request_id_keys(hf_strategy, default_strategy):
    """HF uses 'requestId', default uses 'request_id' — they must not be swapped."""
    data = {"request_id": "req-00000000-0000-0000-0000-000000000001", "status": "pending"}

    hf_response = hf_strategy.format_request_response(data)
    default_response = default_strategy.format_request_response(data)

    assert "requestId" in hf_response, "HF must use 'requestId'"
    assert "request_id" in default_response, "Default must use 'request_id'"
    assert "request_id" not in hf_response, "HF must NOT use 'request_id'"
    assert "requestId" not in default_response, "Default must NOT use 'requestId'"
