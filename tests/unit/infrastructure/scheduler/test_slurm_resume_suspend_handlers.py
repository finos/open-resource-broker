"""Unit tests for SlurmSchedulerStrategy.handle_resume_request/handle_suspend_request.

Covers open-resource-broker-2706.4: these handlers previously had no direct
tests — the integration lifecycle tests exercise a parallel mock service
rather than the real strategy methods.
"""

from typing import Any

import pytest

from orb.infrastructure.scheduler.slurm.slurm_strategy import SlurmSchedulerStrategy


class _FakeSlurmClient:
    def __init__(self, node_to_partition: dict[str, str]) -> None:
        self._node_to_partition = node_to_partition

    def get_node(self, node_name: str) -> dict[str, Any]:
        partition = self._node_to_partition.get(node_name)
        if partition is None:
            return {}
        return {"Partitions": partition}


@pytest.fixture
def strategy() -> SlurmSchedulerStrategy:
    s = SlurmSchedulerStrategy()
    s._slurm_client = _FakeSlurmClient({"compute-001": "compute", "compute-002": "compute"})
    return s


# ---------------------------------------------------------------------------
# handle_resume_request
# ---------------------------------------------------------------------------


def test_handle_resume_request_returns_pending_status(strategy):
    response = strategy.handle_resume_request(["compute-001", "compute-002"])

    assert response["status"] == "pending"
    assert response["request_id"] is None
    assert "2 nodes" in response["message"]
    assert "compute" in response["message"]


def test_handle_resume_request_single_node(strategy):
    response = strategy.handle_resume_request(["compute-001"])

    assert "1 nodes" in response["message"]


def test_handle_resume_request_raises_for_unmapped_partition(strategy):
    with pytest.raises(ValueError, match="Could not determine SLURM partition"):
        strategy.handle_resume_request(["ghost-node"])


# ---------------------------------------------------------------------------
# handle_suspend_request
# ---------------------------------------------------------------------------


def test_handle_suspend_request_terminates_mapped_nodes(strategy):
    strategy.node_mapper.register_mapping("compute-001", "i-aaa111")
    strategy.node_mapper.register_mapping("compute-002", "i-bbb222")

    response = strategy.handle_suspend_request(["compute-001", "compute-002"])

    assert response["status"] == "pending"
    assert "2 instances" in response["message"]


def test_handle_suspend_request_clears_node_mappings(strategy):
    strategy.node_mapper.register_mapping("compute-001", "i-aaa111")

    strategy.handle_suspend_request(["compute-001"])

    assert strategy.node_mapper.get_machine_id("compute-001") is None


def test_handle_suspend_request_handles_unmapped_nodes_gracefully(strategy):
    """Suspending nodes with no registered machine mapping doesn't crash."""
    response = strategy.handle_suspend_request(["never-resumed-node"])

    assert response["status"] == "pending"
    assert "0 instances" in response["message"]


def test_handle_suspend_request_mixed_mapped_and_unmapped(strategy):
    strategy.node_mapper.register_mapping("compute-001", "i-aaa111")

    response = strategy.handle_suspend_request(["compute-001", "never-mapped"])

    assert "1 instances" in response["message"]
    assert strategy.node_mapper.get_machine_id("compute-001") is None
