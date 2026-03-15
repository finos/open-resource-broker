"""Integration tests for scheduler-aware response consistency.

These tests verify both schedulers produce correct output shapes when wired
through real strategy instances (no mocks on the strategies themselves).
Marked with @pytest.mark.integration — excluded from fast unit test runs.
"""

import pytest
from datetime import datetime, timezone

from orb.application.request.dto import RequestDTO
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.dtos import TemplateDTO


HF_ALLOWED_STATUSES = {"running", "complete", "complete_with_error"}


def _make_dto(status: str = "complete") -> RequestDTO:
    return RequestDTO(
        request_id="req-abc",
        status=status,
        requested_count=1,
        created_at=datetime.now(timezone.utc),
    )


def _make_template(template_id: str = "tpl-1") -> TemplateDTO:
    return TemplateDTO(
        template_id=template_id,
        name="Test Template",
        image_id="ami-abc123",
        max_instances=5,
        subnet_ids=["subnet-1"],
        security_group_ids=["sg-1"],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_strategy():
    """Real DefaultSchedulerStrategy instance."""
    return DefaultSchedulerStrategy()


@pytest.fixture
def hf_strategy():
    """Real HostFactorySchedulerStrategy instance."""
    return HostFactorySchedulerStrategy()


@pytest.fixture(params=["default", "hostfactory"])
def any_scheduler(request, default_strategy, hf_strategy):
    """Parametric fixture — runs the same test against both schedulers."""
    if request.param == "default":
        return request.param, default_strategy
    return request.param, hf_strategy


# ---------------------------------------------------------------------------
# 6c. Both schedulers return a dict with a 'requests' list
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_format_request_status_response_both_schedulers(any_scheduler):
    """Both schedulers must return a dict with a 'requests' list."""
    scheduler_type, strategy = any_scheduler

    dto = _make_dto("complete")
    result = strategy.format_request_status_response([dto])

    assert isinstance(result, dict), f"{scheduler_type}: result must be a dict"
    assert "requests" in result, f"{scheduler_type}: result must have 'requests' key"
    assert isinstance(result["requests"], list), f"{scheduler_type}: 'requests' must be a list"
    assert len(result["requests"]) == 1

    req = result["requests"][0]
    if scheduler_type == "hostfactory":
        assert "requestId" in req, "HF must use camelCase requestId"
        assert req["status"] in HF_ALLOWED_STATUSES, (
            f"HF status '{req['status']}' not in allowed set"
        )
    else:
        assert "requestId" not in req, "default must not use camelCase requestId"


@pytest.mark.integration
def test_format_request_status_response_empty_list_both_schedulers(any_scheduler):
    """Both schedulers must handle empty request list without error."""
    scheduler_type, strategy = any_scheduler
    result = strategy.format_request_status_response([])
    assert "requests" in result
    assert result["requests"] == []


@pytest.mark.integration
@pytest.mark.parametrize("status", ["pending", "in_progress", "complete", "failed", "partial"])
def test_format_request_status_all_statuses_both_schedulers(any_scheduler, status):
    """Both schedulers must handle all domain statuses without raising."""
    scheduler_type, strategy = any_scheduler
    dto = _make_dto(status)
    result = strategy.format_request_status_response([dto])
    assert "requests" in result
    assert len(result["requests"]) == 1
    returned_status = result["requests"][0]["status"]
    assert isinstance(returned_status, str)
    assert len(returned_status) > 0


# ---------------------------------------------------------------------------
# format_request_response — both schedulers
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_format_request_response_both_schedulers(any_scheduler):
    """Both schedulers must return a dict from format_request_response."""
    scheduler_type, strategy = any_scheduler
    result = strategy.format_request_response({"request_id": "req-1", "status": "pending"})
    assert isinstance(result, dict)

    if scheduler_type == "hostfactory":
        assert "requestId" in result
        assert "request_id" not in result
    else:
        assert "request_id" in result
        assert "requestId" not in result


# ---------------------------------------------------------------------------
# format_templates_response — both schedulers
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_format_templates_response_both_schedulers(any_scheduler):
    """Both schedulers must return a dict with a 'templates' list."""
    scheduler_type, strategy = any_scheduler
    t = _make_template()
    result = strategy.format_templates_response([t])

    assert isinstance(result, dict)
    assert "templates" in result
    assert isinstance(result["templates"], list)
    assert len(result["templates"]) == 1

    tpl = result["templates"][0]
    if scheduler_type == "hostfactory":
        assert "templateId" in tpl
        assert "maxNumber" in tpl
        assert "attributes" in tpl
    else:
        assert "template_id" in tpl
        assert "total_count" in result


@pytest.mark.integration
def test_format_templates_response_empty_both_schedulers(any_scheduler):
    """Both schedulers must handle empty template list."""
    scheduler_type, strategy = any_scheduler
    result = strategy.format_templates_response([])
    assert "templates" in result
    assert result["templates"] == []


# ---------------------------------------------------------------------------
# Scheduler type identifier
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_scheduler_type_identifiers(default_strategy, hf_strategy):
    """Each strategy must return its correct type identifier."""
    assert default_strategy.get_scheduler_type() == "default"
    assert hf_strategy.get_scheduler_type() == "hostfactory"


# ---------------------------------------------------------------------------
# HF-specific: status vocabulary enforcement across all domain statuses
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("domain_status", [
    "pending", "in_progress", "acquiring", "provisioning",
    "complete", "completed", "partial", "failed", "cancelled", "timeout", "error",
])
def test_hf_status_vocabulary_integration(hf_strategy, domain_status):
    """Every domain status must map to one of the 3 HF-allowed values (integration)."""
    result = hf_strategy.format_request_status_response([_make_dto(domain_status)])
    hf_status = result["requests"][0]["status"]
    assert hf_status in HF_ALLOWED_STATUSES, (
        f"domain status '{domain_status}' mapped to '{hf_status}', not in {HF_ALLOWED_STATUSES}"
    )


# ---------------------------------------------------------------------------
# Default-specific: snake_case throughout
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_default_snake_case_throughout(default_strategy):
    """Default scheduler must use snake_case in all response fields."""
    dto = _make_dto("complete")
    status_result = default_strategy.format_request_status_response([dto])
    req_result = default_strategy.format_request_response({"request_id": "req-1", "status": "pending"})
    tpl_result = default_strategy.format_templates_response([_make_template()])

    # No camelCase keys anywhere
    assert "requestId" not in status_result.get("requests", [{}])[0]
    assert "requestId" not in req_result
    assert "templateId" not in tpl_result.get("templates", [{}])[0]


# ---------------------------------------------------------------------------
# Multiple DTOs — ordering preserved
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_request_ordering_preserved_both_schedulers(any_scheduler):
    """Scheduler must preserve the order of DTOs in the response."""
    scheduler_type, strategy = any_scheduler
    ids = ["req-1", "req-2", "req-3"]
    dtos = [
        RequestDTO(
            request_id=rid,
            status="complete",
            requested_count=1,
            created_at=datetime.now(timezone.utc),
        )
        for rid in ids
    ]
    result = strategy.format_request_status_response(dtos)
    assert len(result["requests"]) == 3

    id_field = "requestId" if scheduler_type == "hostfactory" else "request_id"
    returned_ids = [r[id_field] for r in result["requests"]]
    assert returned_ids == ids, f"{scheduler_type}: request ordering must be preserved"
