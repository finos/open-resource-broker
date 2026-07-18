"""Coverage-gap tests for HostFactoryResponseFormatter.

Targets the branches missed by test_hf_response_contracts.py:
- format_request_response: cancelled, timeout, partial, complete, in_progress, unknown
- format_get_request_status: DTO path, dict path, fallback path
- format_templates_response: tag dict->json, tag None removal, attributes injection
- format_request_status_response: provider fields, timestamp fields, machine_references rename
- format_machine_status_response: MachineDTO formatting
- format_machine_details_response
- format_template_mutation_response
- format_machines_for_hostfactory: request_type return/acquire/neutral, tags, price_type
- map_domain_status_to_hostfactory
- generate_status_message
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from orb.application.dto.responses import MachineDTO
from orb.application.request.dto import RequestDTO
from orb.infrastructure.scheduler.hostfactory.response_formatter import (
    HostFactoryResponseFormatter,
)
from orb.infrastructure.template.dtos import TemplateDTO

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fmt() -> HostFactoryResponseFormatter:
    return HostFactoryResponseFormatter()


def _unwrap_id(raw_id):
    return raw_id


def _coerce(data):
    if isinstance(data, dict):
        return data
    return data.__dict__


def _make_machine_dto(**overrides) -> MachineDTO:
    defaults = dict(
        machine_id="m1",
        name="host1",
        status="running",
        instance_type="t2.micro",
        private_ip="10.0.0.1",
        result="pass",
    )
    defaults.update(overrides)
    return MachineDTO(**defaults)


def _make_request_dto(**overrides) -> RequestDTO:
    defaults = dict(
        request_id="r1",
        status="pending",
        requested_count=2,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return RequestDTO(**defaults)


# ---------------------------------------------------------------------------
# format_request_response — status branches
# ---------------------------------------------------------------------------


class TestFormatRequestResponseBranches:
    def test_cancelled_status(self, fmt):
        result = fmt.format_request_response(
            {"request_id": "r1", "status": "cancelled"}, _unwrap_id, _coerce
        )
        assert result["requestId"] == "r1"
        assert "cancelled" in result["message"].lower()

    def test_timeout_status(self, fmt):
        result = fmt.format_request_response(
            {"request_id": "r2", "status": "timeout"}, _unwrap_id, _coerce
        )
        assert result["requestId"] == "r2"
        assert "timed out" in result["message"].lower()

    def test_partial_status_with_message(self, fmt):
        result = fmt.format_request_response(
            {"request_id": "r3", "status": "partial", "status_message": "5 failed"},
            _unwrap_id,
            _coerce,
        )
        assert result["requestId"] == "r3"
        assert "5 failed" in result["message"]

    def test_partial_status_without_message(self, fmt):
        result = fmt.format_request_response(
            {"request_id": "r3", "status": "partial"}, _unwrap_id, _coerce
        )
        assert "Some resources failed" in result["message"]

    def test_complete_status(self, fmt):
        result = fmt.format_request_response(
            {"request_id": "r4", "status": "complete"}, _unwrap_id, _coerce
        )
        assert "completed successfully" in result["message"].lower()

    def test_in_progress_status(self, fmt):
        result = fmt.format_request_response(
            {"request_id": "r5", "status": "in_progress"}, _unwrap_id, _coerce
        )
        assert "progress" in result["message"].lower()

    def test_unknown_status_uses_message_field(self, fmt):
        result = fmt.format_request_response(
            {"request_id": "r6", "status": "weird", "message": "custom msg"},
            _unwrap_id,
            _coerce,
        )
        assert result["message"] == "custom msg"

    def test_failed_status_no_status_message(self, fmt):
        result = fmt.format_request_response(
            {"request_id": "r7", "status": "failed"}, _unwrap_id, _coerce
        )
        assert "Unknown error" in result["message"]

    def test_requests_list_passthrough(self, fmt):
        """When the response already has a 'requests' key, pass it through."""
        data = {
            "requests": [{"requestId": "r1"}],
            "status": "complete",
            "message": "done",
        }
        result = fmt.format_request_response(data, _unwrap_id, _coerce)
        assert "requests" in result
        assert result["status"] == "complete"


# ---------------------------------------------------------------------------
# format_get_request_status
# ---------------------------------------------------------------------------


class TestFormatGetRequestStatus:
    def _noop_format_machines(self, machines, request_type=None):
        return machines

    def _noop_map_status(self, s):
        return s

    def _noop_generate_msg(self, status, count):
        return f"{status}:{count}"

    def test_dict_path(self, fmt):
        data = {
            "request_id": "r1",
            "status": "complete",
            "machines": [{"machine_id": "m1"}],
        }
        result = fmt.format_get_request_status(
            data,
            self._noop_format_machines,
            self._noop_map_status,
            self._noop_generate_msg,
        )
        assert result["requests"][0]["requestId"] == "r1"
        assert result["requests"][0]["status"] == "complete"

    def test_dict_path_requestId_alias(self, fmt):
        data = {"requestId": "r99", "status": "pending", "machines": []}
        result = fmt.format_get_request_status(
            data,
            self._noop_format_machines,
            self._noop_map_status,
            self._noop_generate_msg,
        )
        assert result["requests"][0]["requestId"] == "r99"

    def test_fallback_returns_empty_requests(self, fmt):
        result = fmt.format_get_request_status(
            None,
            self._noop_format_machines,
            self._noop_map_status,
            self._noop_generate_msg,
        )
        assert result == {"requests": [], "message": "Request not found."}


# ---------------------------------------------------------------------------
# format_templates_response
# ---------------------------------------------------------------------------


class TestFormatTemplatesResponse:
    def _basic_format_template(self, t: TemplateDTO) -> dict:
        return {"template_id": t.template_id, "name": t.name or ""}

    def _build_attributes(self, instance_type: str) -> list:
        return [{"name": "vmType", "value": instance_type}]

    def test_empty_templates(self, fmt):
        result = fmt.format_templates_response(
            [], self._basic_format_template, self._build_attributes
        )
        assert result["templates"] == []
        assert result["total_count"] == 0

    def test_templateId_aliased_from_template_id(self, fmt):
        t = TemplateDTO(template_id="t1")
        result = fmt.format_templates_response(
            [t], self._basic_format_template, self._build_attributes
        )
        formatted = result["templates"][0]
        assert formatted["templateId"] == "t1"

    def test_instance_tags_dict_converted_to_json(self, fmt):
        t = TemplateDTO(template_id="t2")

        def format_with_tags(tmpl):
            return {"template_id": tmpl.template_id, "instanceTags": {"env": "prod"}}

        result = fmt.format_templates_response([t], format_with_tags, self._build_attributes)
        tags = result["templates"][0]["instanceTags"]
        # Dict must have been JSON-encoded
        parsed = json.loads(tags)
        assert parsed["env"] == "prod"

    def test_instance_tags_none_removed(self, fmt):
        t = TemplateDTO(template_id="t3")

        def format_with_none_tags(tmpl):
            return {"template_id": tmpl.template_id, "instanceTags": None}

        result = fmt.format_templates_response([t], format_with_none_tags, self._build_attributes)
        assert "instanceTags" not in result["templates"][0]

    def test_attributes_injected_when_absent(self, fmt):
        t = TemplateDTO(template_id="t4")

        def format_no_attrs(tmpl):
            return {"template_id": tmpl.template_id, "vmType": "m5.xlarge"}

        result = fmt.format_templates_response([t], format_no_attrs, self._build_attributes)
        assert "attributes" in result["templates"][0]

    def test_success_and_message_in_response(self, fmt):
        t = TemplateDTO(template_id="t5")
        result = fmt.format_templates_response(
            [t], self._basic_format_template, self._build_attributes
        )
        assert result["success"] is True
        assert "1 templates" in result["message"]


# ---------------------------------------------------------------------------
# format_request_status_response
# ---------------------------------------------------------------------------


class TestFormatRequestStatusResponse:
    def _noop_format_machines(self, machines, request_type=None):
        return []

    def _noop_map_status(self, s):
        return s

    def test_basic_request_dto(self, fmt):
        r = _make_request_dto()
        result = fmt.format_request_status_response(
            [r], self._noop_format_machines, self._noop_map_status
        )
        assert len(result["requests"]) == 1
        req = result["requests"][0]
        assert req["requestId"] == "r1"
        assert req["status"] == "pending"

    def test_machine_references_renamed_to_machines(self, fmt):
        """When the DTO dict has machine_references, it should be renamed."""
        r = MagicMock()
        r.to_dict.return_value = {
            "request_id": "r1",
            "status": "pending",
            "machine_references": [{"machine_id": "m1"}],
        }
        result = fmt.format_request_status_response(
            [r], self._noop_format_machines, self._noop_map_status
        )
        req = result["requests"][0]
        assert "machine_references" not in req

    def test_provider_fields_included_when_present(self, fmt):
        r = MagicMock()
        r.to_dict.return_value = {
            "request_id": "r1",
            "status": "pending",
            "provider_name": "aws-provider",
            "provider_type": "aws",
            "provider_api": "RunInstances",
        }
        result = fmt.format_request_status_response(
            [r], self._noop_format_machines, self._noop_map_status
        )
        req = result["requests"][0]
        assert req["providerName"] == "aws-provider"
        assert req["providerType"] == "aws"
        assert req["providerApi"] == "RunInstances"

    def test_timestamp_fields_forwarded(self, fmt):
        r = MagicMock()
        r.to_dict.return_value = {
            "request_id": "r1",
            "status": "pending",
            "started_at": "2026-01-01T01:00:00Z",
            "completed_at": "2026-01-01T02:00:00Z",
        }
        result = fmt.format_request_status_response(
            [r], self._noop_format_machines, self._noop_map_status
        )
        req = result["requests"][0]
        assert req["started_at"] == "2026-01-01T01:00:00Z"
        assert req["completed_at"] == "2026-01-01T02:00:00Z"


# ---------------------------------------------------------------------------
# format_machine_status_response
# ---------------------------------------------------------------------------


class TestFormatMachineStatusResponse:
    def test_empty_list(self, fmt):
        result = fmt.format_machine_status_response([])
        assert result == {"machines": []}

    def test_basic_machine_fields(self, fmt):
        m = _make_machine_dto(
            machine_id="m1",
            name="host1",
            template_id="t1",
            request_id="r1",
            instance_type="t2.micro",
            private_ip="10.0.0.1",
            status="running",
        )
        result = fmt.format_machine_status_response([m])
        machine = result["machines"][0]
        assert machine["machineId"] == "m1"
        assert machine["templateId"] == "t1"
        assert machine["requestId"] == "r1"
        assert machine["vmType"] == "t2.micro"
        assert machine["privateIpAddress"] == "10.0.0.1"
        assert machine["status"] == "running"


# ---------------------------------------------------------------------------
# format_machine_details_response
# ---------------------------------------------------------------------------


class TestFormatMachineDetailsResponse:
    def test_all_fields_mapped(self, fmt):
        data = {
            "name": "host1",
            "status": "running",
            "provider_type": "aws",
            "region": "us-east-1",
            "machine_id": "m1",
            "instance_type": "t2.micro",
            "private_ip": "10.0.0.1",
        }
        result = fmt.format_machine_details_response(data)
        assert result["machineId"] == "m1"
        assert result["vmType"] == "t2.micro"
        assert result["privateIp"] == "10.0.0.1"
        assert result["provider"] == "aws"
        assert result["region"] == "us-east-1"

    def test_provider_defaults_to_aws(self, fmt):
        result = fmt.format_machine_details_response({"name": "h"})
        assert result["provider"] == "aws"


# ---------------------------------------------------------------------------
# format_template_mutation_response
# ---------------------------------------------------------------------------


class TestFormatTemplateMutationResponse:
    def test_fields_camelcased(self, fmt):
        raw = {"template_id": "t1", "status": "created", "validation_errors": []}
        result = fmt.format_template_mutation_response(raw)
        assert result["templateId"] == "t1"
        assert result["status"] == "created"
        assert result["validationErrors"] == []

    def test_missing_fields_return_none(self, fmt):
        result = fmt.format_template_mutation_response({})
        assert result["templateId"] is None
        assert result["status"] is None
        assert result["validationErrors"] == []


# ---------------------------------------------------------------------------
# format_machines_for_hostfactory — request_type branches
# ---------------------------------------------------------------------------


class TestFormatMachinesForHostfactory:
    def _machine(self, **overrides) -> dict:
        base = {
            "machine_id": "m1",
            "status": "running",
            "private_ip": "10.0.0.1",
        }
        base.update(overrides)
        return base

    def test_request_type_return_adds_request_id(self, fmt):
        machines = [self._machine(request_id="r1")]
        result = fmt.format_machines_for_hostfactory(machines, request_type="return")
        assert result[0]["requestId"] == "r1"
        assert "returnRequestId" not in result[0]

    def test_request_type_acquire_adds_return_request_id_when_present(self, fmt):
        machines = [self._machine(return_request_id="rr1")]
        result = fmt.format_machines_for_hostfactory(machines, request_type="acquire")
        assert result[0]["returnRequestId"] == "rr1"

    def test_request_type_acquire_no_return_request_id(self, fmt):
        machines = [self._machine()]
        result = fmt.format_machines_for_hostfactory(machines, request_type="acquire")
        assert "returnRequestId" not in result[0]

    def test_neutral_context_shows_both_ids(self, fmt):
        machines = [self._machine(request_id="r1", return_request_id="rr1")]
        result = fmt.format_machines_for_hostfactory(machines, request_type=None)
        assert result[0]["requestId"] == "r1"
        assert result[0]["returnRequestId"] == "rr1"

    def test_tags_json_encoded(self, fmt):
        machines = [self._machine(tags={"env": "prod", "team": "sre"})]
        result = fmt.format_machines_for_hostfactory(machines)
        tags = json.loads(result[0]["instanceTags"])
        assert tags["env"] == "prod"

    def test_instance_type_included_when_present(self, fmt):
        machines = [self._machine(instance_type="m5.large")]
        result = fmt.format_machines_for_hostfactory(machines)
        assert result[0]["instanceType"] == "m5.large"

    def test_price_type_included_when_present(self, fmt):
        machines = [self._machine(price_type="spot")]
        result = fmt.format_machines_for_hostfactory(machines)
        assert result[0]["priceType"] == "spot"

    def test_fail_result_uses_status_reason(self, fmt):
        machines = [self._machine(status="failed", status_reason="quota exceeded")]
        result = fmt.format_machines_for_hostfactory(machines)
        assert result[0]["message"] == "quota exceeded"

    def test_launch_time_zero_when_missing(self, fmt):
        result = fmt.format_machines_for_hostfactory([self._machine()])
        assert result[0]["launchtime"] == 0

    def test_provision_request_type(self, fmt):
        machines = [self._machine(return_request_id="rr2")]
        result = fmt.format_machines_for_hostfactory(machines, request_type="provision")
        assert result[0]["returnRequestId"] == "rr2"

    def test_private_ip_fallback_to_private_ip(self, fmt):
        machines = [{"machine_id": "m1", "status": "running", "private_ip": "192.168.1.1"}]
        result = fmt.format_machines_for_hostfactory(machines)
        assert result[0]["privateIpAddress"] == "192.168.1.1"


# ---------------------------------------------------------------------------
# map_domain_status_to_hostfactory
# ---------------------------------------------------------------------------


class TestMapDomainStatusToHostfactory:
    @pytest.mark.parametrize(
        "domain,expected",
        [
            ("pending", "running"),
            ("in_progress", "running"),
            ("provisioning", "running"),
            ("complete", "complete"),
            ("completed", "complete"),
            ("partial", "complete_with_error"),
            ("failed", "complete_with_error"),
            ("cancelled", "complete_with_error"),
            ("timeout", "complete_with_error"),
            ("error", "complete_with_error"),
            ("unknown_xyz", "running"),
        ],
    )
    def test_status_mapping(self, fmt, domain, expected):
        assert fmt.map_domain_status_to_hostfactory(domain) == expected


# ---------------------------------------------------------------------------
# generate_status_message
# ---------------------------------------------------------------------------


class TestGenerateStatusMessage:
    def test_completed_returns_empty(self, fmt):
        assert fmt.generate_status_message("completed", 5) == ""

    def test_partial_includes_count(self, fmt):
        msg = fmt.generate_status_message("partial", 3)
        assert "3" in msg

    def test_failed_returns_fixed_message(self, fmt):
        assert fmt.generate_status_message("failed", 0) == "Failed to create instances"

    def test_in_progress_returns_empty(self, fmt):
        assert fmt.generate_status_message("in_progress", 0) == ""

    def test_pending_returns_empty(self, fmt):
        assert fmt.generate_status_message("pending", 0) == ""

    def test_unknown_returns_empty(self, fmt):
        assert fmt.generate_status_message("something_else", 2) == ""
