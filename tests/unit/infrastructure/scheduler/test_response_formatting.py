"""Layer 3: Response formatting tests validated against plugin_io_schemas.py.

No file I/O, no DI container, no AWS.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

try:
    import jsonschema

    _JSONSCHEMA_AVAILABLE = True
except ImportError:
    _JSONSCHEMA_AVAILABLE = False

from orb.application.request.dto import MachineReferenceDTO, RequestDTO
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.dtos import TemplateDTO
from tests.onaws.plugin_io_schemas import (
    expected_get_available_templates_schema_default,
    expected_get_available_templates_schema_hostfactory,
    expected_request_status_schema_default,
    expected_request_status_schema_hostfactory,
)
from tests.unit.infrastructure.scheduler.conftest import make_default_strategy, make_hf_strategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_REQUEST_ID = "req-12345678-1234-1234-1234-123456789abc"
_VALID_RETURN_ID = "ret-12345678-1234-1234-1234-123456789abc"
_VALID_INSTANCE_ID = "i-0abcdef1234567890"


def _validate(instance: Any, schema: dict) -> None:
    """Validate instance against schema, skip gracefully if jsonschema not installed."""
    if not _JSONSCHEMA_AVAILABLE:
        pytest.skip("jsonschema not installed")
    jsonschema.validate(instance=instance, schema=schema)


def _make_template_dto(
    template_id: str = "tpl-001",
    max_instances: int = 5,
    machine_types: dict | None = None,
    **kwargs,
) -> TemplateDTO:
    return TemplateDTO(
        template_id=template_id,
        max_instances=max_instances,
        machine_types=machine_types or {"t3.medium": 1},
        **kwargs,
    )


def _make_machine_ref_dict(
    machine_id: str = _VALID_INSTANCE_ID,
    status: str = "running",
    result: str = "succeed",
    private_ip: str = "10.0.1.5",
    launch_time: int = 1700000000,
) -> dict:
    return {
        "machine_id": machine_id,
        "name": machine_id,
        "status": status,
        "result": result,
        "private_ip_address": private_ip,
        "launch_time_timestamp": launch_time,
        "message": "",
        "cloud_host_id": None,
    }


def _make_request_dto(
    request_id: str = _VALID_REQUEST_ID,
    status: str = "pending",
    machines: list[dict] | None = None,
    request_type: str = "acquire",
) -> RequestDTO:
    machine_refs = []
    for m in machines or []:
        machine_refs.append(
            MachineReferenceDTO(
                machine_id=m.get("machine_id", _VALID_INSTANCE_ID),
                name=m.get("name", ""),
                result=m.get("result", "executing"),
                status=m.get("status", "pending"),
                private_ip_address=m.get("private_ip_address", ""),
                launch_time=m.get("launch_time_timestamp"),
                message=m.get("message", ""),
                cloud_host_id=m.get("cloud_host_id"),
            )
        )
    return RequestDTO(
        request_id=request_id,
        status=status,
        requested_count=len(machine_refs) or 1,
        created_at=datetime.now(timezone.utc),
        machine_references=machine_refs,
        request_type=request_type,
    )


# ---------------------------------------------------------------------------
# HF format_templates_response
# ---------------------------------------------------------------------------


def test_hf_format_templates_response_top_level_keys():
    strategy = make_hf_strategy()
    dto = _make_template_dto()
    result = strategy.format_templates_response([dto])
    for key in ("templates", "message", "success", "total_count"):
        assert key in result, f"HF templates response missing key '{key}'"


def test_hf_format_templates_response_template_item_required_keys():
    strategy = make_hf_strategy()
    dto = _make_template_dto()
    result = strategy.format_templates_response([dto])
    item = result["templates"][0]
    for key in ("templateId", "maxNumber", "attributes"):
        assert key in item, f"HF template item missing required key '{key}'"


def test_hf_format_templates_response_attributes_structure():
    strategy = make_hf_strategy()
    dto = _make_template_dto(machine_types={"t3.medium": 1})
    result = strategy.format_templates_response([dto])
    attrs = result["templates"][0]["attributes"]
    for key in ("type", "ncpus", "ncores", "nram"):
        assert key in attrs
        assert isinstance(attrs[key], list)
        assert len(attrs[key]) == 2


def test_hf_format_templates_response_instance_tags_serialised_as_string():
    """instanceTags dict must be JSON-serialised to a string in HF response."""
    from unittest.mock import patch

    strategy = make_hf_strategy()
    dto = _make_template_dto()
    _original_to_dict = TemplateDTO.to_dict

    def patched_to_dict(self):
        d = _original_to_dict(self)
        d["instanceTags"] = {"env": "prod"}
        return d

    with patch.object(TemplateDTO, "to_dict", patched_to_dict):
        result = strategy.format_templates_response([dto])

    item = result["templates"][0]
    if "instanceTags" in item:
        assert isinstance(item["instanceTags"], str), "instanceTags must be a JSON string"


def test_hf_format_templates_response_instance_tags_none_absent():
    """instanceTags is absent when the template has no tags and the mapper produces None.

    The strategy removes instanceTags only when the mapped value is None.
    We test this by patching format_template_for_display to return a dict
    where instanceTags is explicitly None — the strategy must then drop it.
    """
    from unittest.mock import patch

    strategy = make_hf_strategy()
    dto = _make_template_dto()
    _original_fmt = HostFactorySchedulerStrategy.format_template_for_display

    def patched_fmt(self, template):
        d = _original_fmt(self, template)
        d["instanceTags"] = None
        return d

    with patch.object(HostFactorySchedulerStrategy, "format_template_for_display", patched_fmt):
        result = strategy.format_templates_response([dto])

    item = result["templates"][0]
    assert "instanceTags" not in item


def test_hf_format_templates_response_validates_schema():
    strategy = make_hf_strategy()
    dto = _make_template_dto(machine_types={"t3.medium": 1})
    result = strategy.format_templates_response([dto])
    _validate(result, expected_get_available_templates_schema_hostfactory)


def test_hf_format_templates_response_empty_list():
    strategy = make_hf_strategy()
    result = strategy.format_templates_response([])
    assert result["templates"] == []
    assert result["total_count"] == 0


# ---------------------------------------------------------------------------
# Default format_templates_response
# ---------------------------------------------------------------------------


def test_default_format_templates_response_top_level_keys():
    strategy = make_default_strategy()
    dto = _make_template_dto(template_id="default-tpl-001")
    result = strategy.format_templates_response([dto])
    for key in ("templates", "total_count"):
        assert key in result, f"Default templates response missing key '{key}'"


def test_default_format_templates_response_item_required_keys():
    strategy = make_default_strategy()
    dto = _make_template_dto(template_id="default-tpl-001")
    result = strategy.format_templates_response([dto])
    item = result["templates"][0]
    for key in ("template_id", "max_capacity", "instance_type"):
        assert key in item, f"Default template item missing required key '{key}'"


def test_default_format_templates_response_no_camelcase_keys():
    strategy = make_default_strategy()
    dto = _make_template_dto(template_id="default-tpl-001")
    result = strategy.format_templates_response([dto])
    item = result["templates"][0]
    camel_keys = [k for k in item if k != k.lower() and "_" not in k and k[0].islower()]
    assert camel_keys == [], f"camelCase keys in Default template response: {camel_keys}"


def test_default_format_templates_response_validates_schema():
    strategy = make_default_strategy()
    dto = _make_template_dto(template_id="default-tpl-001", provider_api="EC2Fleet")
    result = strategy.format_templates_response([dto])
    _validate(result, expected_get_available_templates_schema_default)


# ---------------------------------------------------------------------------
# HF format_request_status_response
# ---------------------------------------------------------------------------


def test_hf_format_request_status_response_top_level_keys():
    strategy = make_hf_strategy()
    dto = _make_request_dto(status="pending")
    result = strategy.format_request_status_response([dto])
    assert "requests" in result


def test_hf_format_request_status_response_request_item_keys():
    strategy = make_hf_strategy()
    dto = _make_request_dto(request_id=_VALID_REQUEST_ID, status="pending")
    result = strategy.format_request_status_response([dto])
    item = result["requests"][0]
    for key in ("requestId", "status", "message", "machines"):
        assert key in item, f"HF request item missing key '{key}'"


def test_hf_format_request_status_response_status_values():
    """status must be one of the three IBM HF spec values."""
    strategy = make_hf_strategy()
    valid_hf_statuses = {"running", "complete", "complete_with_error"}
    for domain_status in ("pending", "in_progress", "complete", "completed", "failed", "partial"):
        dto = _make_request_dto(status=domain_status)
        result = strategy.format_request_status_response([dto])
        hf_status = result["requests"][0]["status"]
        assert hf_status in valid_hf_statuses, (
            f"domain status '{domain_status}' mapped to invalid HF status '{hf_status}'"
        )


def test_hf_format_request_status_response_machine_keys():
    strategy = make_hf_strategy()
    machine = _make_machine_ref_dict(status="running", result="succeed")
    dto = _make_request_dto(status="pending", machines=[machine])
    result = strategy.format_request_status_response([dto])
    m = result["requests"][0]["machines"][0]
    for key in (
        "machineId",
        "name",
        "result",
        "status",
        "privateIpAddress",
        "launchtime",
        "message",
    ):
        assert key in m, f"HF machine item missing key '{key}'"


def test_hf_format_request_status_response_result_values():
    """result must be one of executing/succeed/fail."""
    strategy = make_hf_strategy()
    valid_results = {"executing", "succeed", "fail"}
    for machine_status in ("running", "pending", "launching", "terminated", "failed", "error"):
        machine = _make_machine_ref_dict(status=machine_status)
        dto = _make_request_dto(status="pending", machines=[machine])
        result = strategy.format_request_status_response([dto])
        m_result = result["requests"][0]["machines"][0]["result"]
        assert m_result in valid_results, (
            f"machine status '{machine_status}' mapped to invalid result '{m_result}'"
        )


def test_hf_format_request_status_response_private_ip_null_not_empty_string():
    """privateIpAddress must be null (not '') when IP is absent."""
    strategy = make_hf_strategy()
    machine = _make_machine_ref_dict(private_ip="")
    dto = _make_request_dto(status="pending", machines=[machine])
    result = strategy.format_request_status_response([dto])
    ip = result["requests"][0]["machines"][0]["privateIpAddress"]
    assert ip is None, f"Expected null privateIpAddress, got '{ip}'"


def test_hf_format_request_status_response_launchtime_is_integer():
    """launchtime must be an integer (Unix timestamp)."""
    strategy = make_hf_strategy()
    machine = _make_machine_ref_dict(launch_time=1700000000)
    dto = _make_request_dto(status="pending", machines=[machine])
    result = strategy.format_request_status_response([dto])
    lt = result["requests"][0]["machines"][0]["launchtime"]
    assert isinstance(lt, int), f"launchtime must be int, got {type(lt)}"


def test_hf_format_request_status_response_cloud_host_id_always_present():
    """cloudHostId key must always be present (value may be null)."""
    strategy = make_hf_strategy()
    machine = _make_machine_ref_dict()
    dto = _make_request_dto(status="pending", machines=[machine])
    result = strategy.format_request_status_response([dto])
    m = result["requests"][0]["machines"][0]
    assert "cloudHostId" in m, "cloudHostId key must always be present"


def test_hf_format_request_status_response_validates_schema():
    strategy = make_hf_strategy()
    machine = _make_machine_ref_dict(status="running", result="succeed")
    dto = _make_request_dto(request_id=_VALID_REQUEST_ID, status="pending", machines=[machine])
    result = strategy.format_request_status_response([dto])
    _validate(result, expected_request_status_schema_hostfactory)


# ---------------------------------------------------------------------------
# Default format_request_status_response
# ---------------------------------------------------------------------------


def test_default_format_request_status_response_top_level_keys():
    strategy = make_default_strategy()
    dto = _make_request_dto(status="pending")
    result = strategy.format_request_status_response([dto])
    assert "requests" in result


def test_default_format_request_status_response_request_item_keys():
    strategy = make_default_strategy()
    dto = _make_request_dto(request_id=_VALID_REQUEST_ID, status="pending")
    result = strategy.format_request_status_response([dto])
    item = result["requests"][0]
    for key in ("request_id", "status", "message", "machines"):
        assert key in item, f"Default request item missing key '{key}'"


def test_default_format_request_status_response_no_camelcase_in_machines():
    strategy = make_default_strategy()
    machine = _make_machine_ref_dict(status="running")
    dto = _make_request_dto(status="pending", machines=[machine])
    result = strategy.format_request_status_response([dto])
    if result["requests"] and result["requests"][0]["machines"]:
        m = result["requests"][0]["machines"][0]
        camel_keys = [k for k in m if k != k.lower() and "_" not in k and k[0].islower()]
        assert camel_keys == [], f"camelCase keys in Default machine response: {camel_keys}"


def test_default_format_request_status_response_status_values():
    """Default strategy inherits base _map_status_to_hf — same three valid values."""
    strategy = make_default_strategy()
    valid_hf_statuses = {"running", "complete", "complete_with_error"}
    for domain_status in ("pending", "in_progress", "complete", "completed", "failed"):
        dto = _make_request_dto(status=domain_status)
        result = strategy.format_request_status_response([dto])
        hf_status = result["requests"][0]["status"]
        assert hf_status in valid_hf_statuses, (
            f"Default: domain status '{domain_status}' → invalid '{hf_status}'"
        )


def test_default_format_request_status_response_validates_schema():
    strategy = make_default_strategy()
    machine = _make_machine_ref_dict(status="running", result="succeed")
    dto = _make_request_dto(request_id=_VALID_REQUEST_ID, status="pending", machines=[machine])
    result = strategy.format_request_status_response([dto])
    _validate(result, expected_request_status_schema_default)


# ---------------------------------------------------------------------------
# HF format_request_response (requestMachines output)
# ---------------------------------------------------------------------------


def test_hf_format_request_response_pending_status():
    strategy = make_hf_strategy()
    result = strategy.format_request_response(
        {"request_id": _VALID_REQUEST_ID, "status": "pending"}
    )
    assert "requestId" in result
    assert "message" in result
    assert result["requestId"] == _VALID_REQUEST_ID


def test_hf_format_request_response_failed_status():
    strategy = make_hf_strategy()
    result = strategy.format_request_response(
        {
            "request_id": _VALID_REQUEST_ID,
            "status": "failed",
            "status_message": "out of capacity",
        }
    )
    assert "requestId" in result
    assert "failed" in result["message"].lower() or "Request failed" in result["message"]


def test_hf_format_request_response_complete_status():
    strategy = make_hf_strategy()
    result = strategy.format_request_response(
        {
            "request_id": _VALID_REQUEST_ID,
            "status": "complete",
        }
    )
    assert "requestId" in result
    assert result["requestId"] == _VALID_REQUEST_ID


def test_hf_format_request_response_no_snake_case_request_id():
    """HF format_request_response must use requestId not request_id."""
    strategy = make_hf_strategy()
    result = strategy.format_request_response(
        {"request_id": _VALID_REQUEST_ID, "status": "pending"}
    )
    assert "requestId" in result
    assert "request_id" not in result


# ---------------------------------------------------------------------------
# Default format_request_response
# ---------------------------------------------------------------------------


def test_default_format_request_response_pending_status():
    strategy = make_default_strategy()
    result = strategy.format_request_response(
        {"request_id": _VALID_REQUEST_ID, "status": "pending"}
    )
    assert "request_id" in result
    assert result["request_id"] == _VALID_REQUEST_ID


def test_default_format_request_response_failed_status():
    strategy = make_default_strategy()
    result = strategy.format_request_response(
        {
            "request_id": _VALID_REQUEST_ID,
            "status": "failed",
            "status_message": "quota exceeded",
        }
    )
    assert "request_id" in result
    assert "error" in result or "message" in result


def test_default_format_request_response_no_camelcase_request_id():
    """Default format_request_response must use request_id not requestId."""
    strategy = make_default_strategy()
    result = strategy.format_request_response(
        {"request_id": _VALID_REQUEST_ID, "status": "pending"}
    )
    assert "request_id" in result
    assert "requestId" not in result
