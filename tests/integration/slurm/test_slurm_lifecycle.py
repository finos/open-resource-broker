"""SLURM scheduler lifecycle integration tests.

Tests the full resume/suspend lifecycle using mock SLURM responses.
No real SLURM cluster needed.
"""

import uuid

import pytest

from orb.infrastructure.scheduler.slurm.node_mapper import SlurmNodeMapper
from orb.infrastructure.scheduler.slurm.slurm_strategy import SlurmSchedulerStrategy
from orb.infrastructure.template.dtos import TemplateDTO


class MockSlurmAppService:
    """Thin adapter wrapping mock provider with SLURM-style API."""

    def __init__(self):
        self._strategy = SlurmSchedulerStrategy()
        self._node_mapper = SlurmNodeMapper()
        self._requests: dict[str, dict] = {}
        self._templates = [
            TemplateDTO(
                template_id="gpu-partition",
                max_instances=10,
                machine_types={"p3.2xlarge": 1},
                subnet_ids=["subnet-aaa"],
                security_group_ids=["sg-111"],
                price_type="ondemand",
                provider_type="aws",
            ),
            TemplateDTO(
                template_id="compute-partition",
                max_instances=100,
                machine_types={"c5.xlarge": 1},
                subnet_ids=["subnet-bbb"],
                security_group_ids=["sg-222"],
                price_type="spot",
                provider_type="aws",
            ),
        ]

    def get_available_templates(self) -> dict:
        return self._strategy.format_templates_response(self._templates)

    def resume_nodes(self, node_names: list[str], template_id: str = "compute-partition") -> dict:
        """Simulate ResumeProgram — request machines for given nodes."""
        template_ids = [t.template_id for t in self._templates]
        if template_id not in template_ids:
            raise ValueError(f"Template/partition not found: {template_id}")

        req_id = f"req-{uuid.uuid4()}"
        machines = []
        for node_name in node_names:
            machine_id = f"i-{uuid.uuid4().hex[:12]}"
            self._node_mapper.register_mapping(node_name, machine_id)
            machines.append(
                {
                    "machine_id": machine_id,
                    "node_name": node_name,
                    "status": "running",
                    "instance_type": "c5.xlarge",
                    "private_ip_address": "10.0.1.1",
                    "result": "succeed",
                }
            )

        self._requests[req_id] = {
            "request_id": req_id,
            "status": "complete",
            "machines": machines,
            "message": f"Resumed {len(node_names)} nodes",
        }

        return self._strategy.format_request_response(self._requests[req_id])

    def get_request_status(self, request_id: str) -> dict:
        if request_id not in self._requests:
            raise ValueError(f"Request not found: {request_id}")
        return self._strategy.format_request_status_response([self._requests[request_id]])

    def suspend_nodes(self, node_names: list[str]) -> dict:
        """Simulate SuspendProgram — return machines for given nodes."""
        req_id = f"ret-{uuid.uuid4()}"
        machines = []
        for node_name in node_names:
            machine_id = self._node_mapper.get_machine_id(node_name)
            if machine_id:
                machines.append(
                    {
                        "machine_id": machine_id,
                        "node_name": node_name,
                        "status": "terminated",
                        "result": "succeed",
                    }
                )
                self._node_mapper.remove_mapping(node_name)

        self._requests[req_id] = {
            "request_id": req_id,
            "status": "complete",
            "machines": machines,
            "message": f"Suspended {len(machines)} nodes",
        }

        return self._strategy.format_request_response(self._requests[req_id])


@pytest.fixture
def slurm_app():
    return MockSlurmAppService()


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_complete_resume_suspend_lifecycle(slurm_app):
    """Full lifecycle: templates → resume → status → suspend → status."""
    # Get templates
    templates = slurm_app.get_available_templates()
    assert len(templates["templates"]) == 2

    # Resume nodes
    resume_resp = slurm_app.resume_nodes(["compute-001", "compute-002"], "compute-partition")
    assert resume_resp["status"] == "complete"
    req_id = resume_resp["request_id"]

    # Check status
    status = slurm_app.get_request_status(req_id)
    assert status["requests"][0]["status"] == "complete"

    # Suspend nodes
    suspend_resp = slurm_app.suspend_nodes(["compute-001", "compute-002"])
    assert suspend_resp["status"] == "complete"


@pytest.mark.integration
def test_resume_with_nonexistent_template_fails(slurm_app):
    """Request with invalid template raises error."""
    with pytest.raises(ValueError, match="not found"):
        slurm_app.resume_nodes(["node-1"], "nonexistent-partition")


@pytest.mark.integration
def test_suspend_unknown_nodes_fails_gracefully(slurm_app):
    """Suspending unmapped nodes handles gracefully (no crash)."""
    resp = slurm_app.suspend_nodes(["unknown-node-999"])
    # Should succeed but with no machines terminated
    assert resp["status"] == "complete"


@pytest.mark.integration
def test_node_mapper_persists_across_operations(slurm_app):
    """Node mappings registered during resume are available during suspend."""
    slurm_app.resume_nodes(["persist-node-1"], "compute-partition")
    machine_id = slurm_app._node_mapper.get_machine_id("persist-node-1")
    assert machine_id is not None
    assert machine_id.startswith("i-")

    # Suspend should find and use the mapping
    resp = slurm_app.suspend_nodes(["persist-node-1"])
    assert resp["status"] == "complete"
    # Mapping should be removed after suspend
    assert slurm_app._node_mapper.get_machine_id("persist-node-1") is None


# ---------------------------------------------------------------------------
# Response format tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_resume_response_uses_snake_case(slurm_app):
    """Verify all response keys are snake_case."""
    resp = slurm_app.resume_nodes(["node-1"], "compute-partition")
    assert "request_id" in resp
    assert "requestId" not in resp
    assert "status" in resp


@pytest.mark.integration
def test_templates_response_structure(slurm_app):
    """Verify template list response has correct envelope."""
    resp = slurm_app.get_available_templates()
    assert "templates" in resp
    assert "message" in resp
    assert "count" in resp
    assert resp["count"] == 2
    for tpl in resp["templates"]:
        assert "template_id" in tpl


# ---------------------------------------------------------------------------
# Node mapper integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_node_range_expansion_in_resume(slurm_app):
    """Resume with expanded node range processes correct count."""
    node_names = SlurmNodeMapper.expand_node_range("compute-[001-003]")
    resp = slurm_app.resume_nodes(node_names, "compute-partition")
    assert resp["status"] == "complete"
    # All 3 nodes should be mapped
    for name in ["compute-001", "compute-002", "compute-003"]:
        assert slurm_app._node_mapper.get_machine_id(name) is not None


@pytest.mark.integration
def test_multiple_resume_suspend_cycles(slurm_app):
    """Multiple cycles don't corrupt state."""
    for i in range(3):
        nodes = [f"cycle-node-{i}"]
        slurm_app.resume_nodes(nodes, "gpu-partition")
        assert slurm_app._node_mapper.get_machine_id(f"cycle-node-{i}") is not None
        slurm_app.suspend_nodes(nodes)
        assert slurm_app._node_mapper.get_machine_id(f"cycle-node-{i}") is None

    # State should be clean
    assert slurm_app._node_mapper.get_all_mappings() == {}
