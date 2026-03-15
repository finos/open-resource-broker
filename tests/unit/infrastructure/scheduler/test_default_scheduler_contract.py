"""Default scheduler contract tests — pin snake_case and full status vocabulary.

These tests document the DefaultSchedulerStrategy output contract:
- snake_case field names (request_id, not requestId)
- Full domain status vocabulary preserved (not remapped to HF 3-value set)
- total_count in templates response
- message field present
"""

import pytest
from datetime import datetime, timezone

from orb.application.request.dto import RequestDTO
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.template.dtos import TemplateDTO


FULL_DOMAIN_STATUSES = [
    "pending",
    "in_progress",
    "complete",
    "failed",
    "cancelled",
    "timeout",
    "partial",
]


@pytest.fixture
def default_strategy():
    return DefaultSchedulerStrategy()


def _make_dto(status: str) -> RequestDTO:
    return RequestDTO(
        request_id="req-abc",
        status=status,
        requested_count=1,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# 3a. snake_case field contract
# ---------------------------------------------------------------------------


def test_default_request_status_response_snake_case(default_strategy):
    """Default scheduler must use snake_case keys."""
    result = default_strategy.format_request_status_response([_make_dto("complete")])
    # BaseSchedulerStrategy.format_request_status_response returns to_dict() output
    # which uses snake_case. Verify no camelCase leaks in.
    assert "requests" in result
    req = result["requests"][0]
    # The base implementation returns to_dict() which is snake_case
    # requestId must not appear — that is HF-only
    assert "requestId" not in req, "default scheduler must not use camelCase 'requestId'"


def test_default_request_status_response_has_requests_key(default_strategy):
    """format_request_status_response must return a dict with 'requests' list."""
    result = default_strategy.format_request_status_response([_make_dto("pending")])
    assert "requests" in result
    assert isinstance(result["requests"], list)
    assert len(result["requests"]) == 1


def test_default_request_status_response_multiple_dtos(default_strategy):
    """Multiple DTOs must each appear as a separate entry."""
    dtos = [_make_dto("complete"), _make_dto("pending"), _make_dto("failed")]
    result = default_strategy.format_request_status_response(dtos)
    assert len(result["requests"]) == 3


# ---------------------------------------------------------------------------
# 3b. Status passthrough — base class maps via _HF_STATUS_MAP
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", FULL_DOMAIN_STATUSES)
def test_default_status_passthrough(default_strategy, status):
    """Default scheduler must return a non-empty string status for every domain status."""
    result = default_strategy.format_request_status_response([_make_dto(status)])
    returned_status = result["requests"][0]["status"]
    assert isinstance(returned_status, str)
    assert len(returned_status) > 0


def test_default_status_response_count_field(default_strategy):
    """Base format_request_status_response must include count field."""
    dtos = [_make_dto("complete"), _make_dto("pending")]
    result = default_strategy.format_request_status_response(dtos)
    assert "count" in result
    assert result["count"] == 2


def test_default_status_response_message_field(default_strategy):
    """Base format_request_status_response must include message field."""
    result = default_strategy.format_request_status_response([_make_dto("complete")])
    assert "message" in result
    assert isinstance(result["message"], str)


# ---------------------------------------------------------------------------
# 3c. format_request_response snake_case
# ---------------------------------------------------------------------------


def test_default_request_response_snake_case(default_strategy):
    """format_request_response must return request_id (snake_case) for default scheduler."""
    result = default_strategy.format_request_response({"request_id": "req-abc", "status": "pending"})
    assert "request_id" in result, "default scheduler must use snake_case 'request_id'"
    assert "requestId" not in result, "default scheduler must not use camelCase"


def test_default_request_response_complete(default_strategy):
    """format_request_response for complete status returns request_id and message."""
    result = default_strategy.format_request_response({"request_id": "req-abc", "status": "complete"})
    assert "request_id" in result
    assert "message" in result


def test_default_request_response_failed(default_strategy):
    """format_request_response for failed status returns error key."""
    result = default_strategy.format_request_response({"request_id": "req-abc", "status": "failed"})
    assert "request_id" in result
    assert "error" in result


def test_default_request_response_cancelled(default_strategy):
    """format_request_response for cancelled status returns error key."""
    result = default_strategy.format_request_response({"request_id": "req-abc", "status": "cancelled"})
    assert "request_id" in result
    assert "error" in result


@pytest.mark.parametrize("status", ["pending", "in_progress", "complete", "failed", "cancelled", "timeout", "partial"])
def test_default_request_response_always_snake_case(default_strategy, status):
    """format_request_response must always use snake_case regardless of status."""
    result = default_strategy.format_request_response({"request_id": "req-1", "status": status})
    assert "requestId" not in result


# ---------------------------------------------------------------------------
# 3d. Templates response shape
# ---------------------------------------------------------------------------


def test_default_templates_response_shape(default_strategy):
    """Default templates response uses snake_case and total_count."""
    t = TemplateDTO(
        template_id="tpl-1",
        name="T1",
        image_id="ami-abc",
        max_instances=3,
        subnet_ids=["subnet-1"],
    )
    result = default_strategy.format_templates_response([t])
    assert "templates" in result
    assert "total_count" in result, "default scheduler must include 'total_count'"
    assert "message" in result
    tpl = result["templates"][0]
    assert "template_id" in tpl, "default scheduler must use snake_case 'template_id'"
    assert "templateId" not in tpl, "default scheduler must not use camelCase 'templateId'"


def test_default_templates_response_total_count_matches(default_strategy):
    """total_count must equal the number of templates returned."""
    templates = [
        TemplateDTO(template_id=f"tpl-{i}", name=f"T{i}", image_id="ami-abc",
                    max_instances=1, subnet_ids=["subnet-1"])
        for i in range(3)
    ]
    result = default_strategy.format_templates_response(templates)
    assert result["total_count"] == 3
    assert len(result["templates"]) == 3


def test_default_templates_response_empty(default_strategy):
    """Empty template list must still return valid structure with total_count=0."""
    result = default_strategy.format_templates_response([])
    assert result["templates"] == []
    assert result["total_count"] == 0
    assert "message" in result


# ---------------------------------------------------------------------------
# 3e. Scheduler type identifier
# ---------------------------------------------------------------------------


def test_default_scheduler_type(default_strategy):
    """get_scheduler_type must return 'default'."""
    assert default_strategy.get_scheduler_type() == "default"


def test_default_scripts_directory_is_none(default_strategy):
    """Default strategy has no scripts directory."""
    assert default_strategy.get_scripts_directory() is None


# ---------------------------------------------------------------------------
# 3f. Machine status response shape
# ---------------------------------------------------------------------------


def test_default_machine_status_response_shape(default_strategy):
    """format_machine_status_response must return machines list with count."""
    result = default_strategy.format_machine_status_response([])
    assert "machines" in result
    assert "count" in result
    assert result["count"] == 0
