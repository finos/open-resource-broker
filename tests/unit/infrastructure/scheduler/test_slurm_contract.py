"""SLURM contract tests — pin the wire protocol and state mapping exhaustiveness.

These tests ensure:
- SLURM uses snake_case field names (not camelCase like HF)
- Domain statuses pass through unchanged
- Node state mappings are exhaustive
- Node range expansion works correctly
"""

from datetime import datetime, timezone

import pytest

from orb.application.request.dto import RequestDTO
from orb.domain.request.request_types import RequestStatus
from orb.infrastructure.scheduler.slurm.field_mappings import (
    SLURM_NODE_STATE_TO_ORB_MACHINE_STATUS,
)
from orb.infrastructure.scheduler.slurm.node_mapper import SlurmNodeMapper
from orb.infrastructure.scheduler.slurm.slurm_strategy import SlurmSchedulerStrategy
from orb.infrastructure.template.dtos import TemplateDTO


@pytest.fixture
def slurm_strategy():
    return SlurmSchedulerStrategy()


# ---------------------------------------------------------------------------
# 1. Status vocabulary — pass-through domain statuses
# ---------------------------------------------------------------------------

SLURM_ALLOWED_STATUSES = {
    "pending",
    "in_progress",
    "complete",
    "failed",
    "cancelled",
    "timeout",
    "partial",
    "acquiring",
}


def _make_dto(status: str) -> RequestDTO:
    return RequestDTO(
        request_id="req-abc",
        status=status,
        requested_count=1,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize("domain_status", [s.value for s in RequestStatus])
def test_slurm_all_domain_statuses_produce_valid_output(slurm_strategy, domain_status):
    """Every RequestStatus value passes through as a valid SLURM output status."""
    result = slurm_strategy.format_request_status_response([_make_dto(domain_status)])
    status = result["requests"][0]["status"]
    assert status == domain_status, (
        f"SLURM should pass through domain status '{domain_status}', got '{status}'"
    )


# ---------------------------------------------------------------------------
# 2. Node state mapping exhaustiveness
# ---------------------------------------------------------------------------

ALL_SLURM_STATES = [
    "IDLE",
    "ALLOCATED",
    "MIXED",
    "DOWN",
    "DRAIN",
    "DRAINED",
    "ERROR",
    "FAIL",
    "COMPLETING",
    "POWERED_DOWN",
    "POWERING_UP",
    "POWERING_DOWN",
    "RESERVED",
    "FUTURE",
    "PLANNED",
    "NOT_RESPONDING",
    "CONFIGURING",
]

VALID_ORB_MACHINE_STATUSES = {
    "available",
    "running",
    "launching",
    "terminated",
    "failed",
    "pending",
}


@pytest.mark.parametrize("slurm_state", ALL_SLURM_STATES)
def test_slurm_node_state_mapping_exhaustive(slurm_state):
    """Every known SLURM node state maps to a valid ORB machine status."""
    orb_status = SLURM_NODE_STATE_TO_ORB_MACHINE_STATUS.get(slurm_state)
    assert orb_status is not None, f"SLURM state '{slurm_state}' has no mapping"
    assert orb_status in VALID_ORB_MACHINE_STATUSES, (
        f"SLURM state '{slurm_state}' maps to '{orb_status}' not in {VALID_ORB_MACHINE_STATUSES}"
    )


def test_slurm_unknown_state_fallback():
    """Unknown SLURM state should map to 'failed' via field mapper."""
    from orb.infrastructure.scheduler.slurm.field_mapper import SlurmFieldMapper

    result = SlurmFieldMapper.map_node_state("TOTALLY_UNKNOWN")
    assert result == "failed"


def test_slurm_empty_state_fallback():
    """Empty state string should map to 'failed'."""
    from orb.infrastructure.scheduler.slurm.field_mapper import SlurmFieldMapper

    result = SlurmFieldMapper.map_node_state("")
    assert result == "failed"


def test_slurm_compound_state_drain_flag():
    """Compound state with DRAIN flag should map to 'failed'."""
    from orb.infrastructure.scheduler.slurm.field_mapper import SlurmFieldMapper

    result = SlurmFieldMapper.map_node_state("IDLE+DRAIN")
    assert result == "failed"


# ---------------------------------------------------------------------------
# 3. Field name contract — snake_case
# ---------------------------------------------------------------------------


def test_slurm_templates_response_snake_case(slurm_strategy):
    """format_templates_response must use snake_case keys."""
    t = TemplateDTO(template_id="tpl-1", max_instances=5, subnet_ids=["subnet-1"])
    result = slurm_strategy.format_templates_response([t])
    assert "templates" in result
    tpl = result["templates"][0]
    assert "template_id" in tpl, "SLURM must use snake_case 'template_id'"
    assert "templateId" not in tpl, "SLURM must NOT use camelCase"


def test_slurm_request_status_response_snake_case(slurm_strategy):
    """format_request_status_response must use snake_case keys."""
    result = slurm_strategy.format_request_status_response([_make_dto("complete")])
    req = result["requests"][0]
    assert "request_id" in req, "SLURM must use snake_case 'request_id'"
    assert "requestId" not in req, "SLURM must NOT use camelCase"


def test_slurm_machine_status_response_snake_case(slurm_strategy):
    """format_machine_status_response must use snake_case keys."""
    machines = [{"machine_id": "i-abc", "name": "node-1", "status": "running"}]
    result = slurm_strategy.format_machine_status_response(machines)
    machine = result["machines"][0]
    assert "machine_id" in machine, "SLURM must use snake_case 'machine_id'"
    assert "machineId" not in machine, "SLURM must NOT use camelCase"


# ---------------------------------------------------------------------------
# 4. Template response required fields
# ---------------------------------------------------------------------------


def test_slurm_templates_response_envelope(slurm_strategy):
    """Templates response must have 'templates' and 'message' keys."""
    result = slurm_strategy.format_templates_response([])
    assert "templates" in result
    assert "message" in result


def test_slurm_templates_response_required_fields(slurm_strategy):
    """Each template must have template_id and max_instances."""
    t = TemplateDTO(template_id="tpl-1", max_instances=3, subnet_ids=["subnet-1"])
    result = slurm_strategy.format_templates_response([t])
    tpl = result["templates"][0]
    assert "template_id" in tpl
    assert "max_instances" in tpl


# ---------------------------------------------------------------------------
# 5. Node mapper contract
# ---------------------------------------------------------------------------


def test_node_mapper_expand_range():
    """Range expansion: compute-[001-003] → 3 nodes."""
    result = SlurmNodeMapper.expand_node_range("compute-[001-003]")
    assert result == ["compute-001", "compute-002", "compute-003"]


def test_node_mapper_expand_comma_range():
    """Comma ranges: node-[1,3,5-7] → 5 nodes."""
    result = SlurmNodeMapper.expand_node_range("node-[1,3,5-7]")
    assert result == ["node-1", "node-3", "node-5", "node-6", "node-7"]


def test_node_mapper_single_node():
    """Single node passthrough."""
    result = SlurmNodeMapper.expand_node_range("compute-001")
    assert result == ["compute-001"]


def test_node_mapper_space_separated():
    """Space-separated nodes."""
    result = SlurmNodeMapper.expand_node_range("compute-001 compute-002")
    assert result == ["compute-001", "compute-002"]


def test_node_mapper_invalid_name_raises():
    """Invalid node names raise ValueError on register."""
    mapper = SlurmNodeMapper()
    with pytest.raises(ValueError):
        mapper.register_mapping("node;rm -rf /", "i-abc")


def test_node_mapper_invalid_machine_id_raises():
    """Invalid machine IDs raise ValueError on register."""
    mapper = SlurmNodeMapper()
    with pytest.raises(ValueError):
        mapper.register_mapping("node-1", "i-abc;DROP TABLE")


def test_node_mapper_round_trip():
    """Register and retrieve mappings in both directions."""
    mapper = SlurmNodeMapper()
    mapper.register_mapping("compute-001", "i-abc123")
    assert mapper.get_machine_id("compute-001") == "i-abc123"
    assert mapper.get_node_name("i-abc123") == "compute-001"
