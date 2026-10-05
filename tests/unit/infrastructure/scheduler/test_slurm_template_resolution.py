"""Tests for SLURM partition→template resolution on resume.

Covers open-resource-broker-2706.1: `_resolve_template_for_nodes` previously
ignored the node's partition and always returned a hardcoded "default"
template, silently provisioning the wrong instance type/AMI on multi-partition
clusters.
"""

from typing import Any

import pytest

from orb.infrastructure.scheduler.slurm.slurm_strategy import SlurmSchedulerStrategy


class _FakeSlurmClient:
    """Stand-in for SlurmCliAdapter/SlurmRestClient's get_node() shape."""

    def __init__(self, node_to_payload: dict[str, dict[str, Any]]) -> None:
        self._node_to_payload = node_to_payload

    def get_node(self, node_name: str) -> dict[str, Any]:
        return self._node_to_payload.get(node_name, {})


class _FakeConfigManager:
    def __init__(self, scheduler_config: dict[str, Any]) -> None:
        self._scheduler_config = scheduler_config

    def get_configuration_value(self, key: str, default: Any = None) -> Any:
        if key == "scheduler":
            return self._scheduler_config
        return default


@pytest.fixture
def strategy() -> SlurmSchedulerStrategy:
    return SlurmSchedulerStrategy()


def _cli_shaped_payload(partition: str) -> dict[str, str]:
    """Mimic SlurmCliAdapter._parse_scontrol_output for `scontrol show node`."""
    return {"NodeName": "node", "Partitions": partition, "State": "IDLE"}


def _rest_shaped_payload(partition: str) -> dict[str, Any]:
    """Mimic slurmrestd's GET /node/{name} response shape."""
    return {"nodes": [{"partitions": [partition]}]}


# ---------------------------------------------------------------------------
# Multi-partition resolution — distinct templates per partition
# ---------------------------------------------------------------------------


def test_resolve_template_for_gpu_partition_cli_shape(strategy):
    strategy._slurm_client = _FakeSlurmClient({"gpu-001": _cli_shaped_payload("gpu")})

    template = strategy._resolve_template_for_nodes(["gpu-001"])

    assert template.template_id == "gpu"


def test_resolve_template_for_compute_partition_cli_shape(strategy):
    strategy._slurm_client = _FakeSlurmClient({"compute-001": _cli_shaped_payload("compute")})

    template = strategy._resolve_template_for_nodes(["compute-001"])

    assert template.template_id == "compute"


def test_resolve_template_gpu_vs_compute_distinct_templates(strategy):
    """Multi-partition cluster: gpu and compute nodes resolve to distinct templates."""
    strategy._slurm_client = _FakeSlurmClient(
        {
            "gpu-001": _cli_shaped_payload("gpu"),
            "compute-001": _cli_shaped_payload("compute"),
        }
    )

    gpu_template = strategy._resolve_template_for_nodes(["gpu-001"])
    compute_template = strategy._resolve_template_for_nodes(["compute-001"])

    assert gpu_template.template_id == "gpu"
    assert compute_template.template_id == "compute"
    assert gpu_template.template_id != compute_template.template_id


def test_resolve_template_rest_shape(strategy):
    """slurmrestd-style payload (nodes[].partitions list) is also handled."""
    strategy._slurm_client = _FakeSlurmClient({"gpu-001": _rest_shaped_payload("gpu")})

    template = strategy._resolve_template_for_nodes(["gpu-001"])

    assert template.template_id == "gpu"


def test_resolve_template_max_instances_matches_node_count(strategy):
    strategy._slurm_client = _FakeSlurmClient({"compute-001": _cli_shaped_payload("compute")})

    template = strategy._resolve_template_for_nodes(["compute-001", "compute-002", "compute-003"])

    assert template.max_instances == 3


def test_resolve_template_uses_only_first_node(strategy):
    """SLURM guarantees a single ResumeProgram batch is from one partition."""
    strategy._slurm_client = _FakeSlurmClient({"compute-001": _cli_shaped_payload("compute")})

    template = strategy._resolve_template_for_nodes(["compute-001", "compute-002"])

    assert template.template_id == "compute"


# ---------------------------------------------------------------------------
# Config-driven partition → template_id override
# ---------------------------------------------------------------------------


def test_resolve_template_honors_config_override(strategy):
    strategy._slurm_client = _FakeSlurmClient({"gpu-001": _cli_shaped_payload("gpu")})
    strategy._config_manager = _FakeConfigManager(
        {"slurm": {"partitions": {"gpu": {"template_id": "gpu-a100-template"}}}}
    )

    template = strategy._resolve_template_for_nodes(["gpu-001"])

    assert template.template_id == "gpu-a100-template"


def test_resolve_template_falls_back_to_partition_name_without_override(strategy):
    strategy._slurm_client = _FakeSlurmClient({"gpu-001": _cli_shaped_payload("gpu")})
    strategy._config_manager = _FakeConfigManager({"slurm": {"partitions": {}}})

    template = strategy._resolve_template_for_nodes(["gpu-001"])

    assert template.template_id == "gpu"


# ---------------------------------------------------------------------------
# Unmapped partition — explicit error, no silent "default" fallback
# ---------------------------------------------------------------------------


def test_resolve_template_raises_when_partition_unknown(strategy):
    strategy._slurm_client = _FakeSlurmClient({})  # get_node returns {}

    with pytest.raises(ValueError, match="Could not determine SLURM partition"):
        strategy._resolve_template_for_nodes(["unknown-001"])


def test_resolve_template_raises_on_empty_node_list(strategy):
    with pytest.raises(ValueError, match="requires at least one node name"):
        strategy._resolve_template_for_nodes([])


def test_resolve_template_raises_when_client_errors(strategy):
    class _ExplodingClient:
        def get_node(self, node_name: str) -> dict[str, Any]:
            raise RuntimeError("slurmrestd unreachable")

    strategy._slurm_client = _ExplodingClient()

    with pytest.raises(ValueError, match="Could not determine SLURM partition"):
        strategy._resolve_template_for_nodes(["gpu-001"])


# ---------------------------------------------------------------------------
# handle_resume_request end-to-end with real partition resolution
# ---------------------------------------------------------------------------


def test_handle_resume_request_uses_resolved_partition_template(strategy):
    strategy._slurm_client = _FakeSlurmClient({"gpu-001": _cli_shaped_payload("gpu")})

    response = strategy.handle_resume_request(["gpu-001"])

    assert response["status"] == "pending"
    assert "gpu" in response["message"]


def test_handle_resume_request_propagates_unmapped_partition_error(strategy):
    strategy._slurm_client = _FakeSlurmClient({})

    with pytest.raises(ValueError, match="Could not determine SLURM partition"):
        strategy.handle_resume_request(["unknown-001"])


# ---------------------------------------------------------------------------
# _extract_partition_name — payload shape edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, None),
        ({"Partitions": ""}, None),
        ({"Partitions": "gpu,compute"}, "gpu"),
        ({"Partitions": "gpu"}, "gpu"),
        ({"nodes": []}, None),
        ({"nodes": [{"partitions": []}]}, None),
        ({"nodes": [{"partitions": ["gpu", "compute"]}]}, "gpu"),
        ({"nodes": [{}]}, None),
        ({"nodes": ["not-a-dict"]}, None),
    ],
)
def test_extract_partition_name_edge_cases(payload, expected):
    assert SlurmSchedulerStrategy._extract_partition_name(payload) == expected
