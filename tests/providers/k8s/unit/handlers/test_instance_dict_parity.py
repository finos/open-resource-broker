"""Tests for AWS-parity fields in the k8s instance dict.

Covers private_dns_name (always derivable) and provider_data.vcpus (from the
node cache), plus the CPU-quantity parser.
"""

from __future__ import annotations

from types import SimpleNamespace

from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
    _cpu_quantity_to_vcpus,
    _pod_private_dns_name,
    instance_dict_for_pod,
)


def test_cpu_quantity_plain_cores() -> None:
    assert _cpu_quantity_to_vcpus("32") == 32


def test_cpu_quantity_millicores_rounds_up() -> None:
    assert _cpu_quantity_to_vcpus("32000m") == 32
    assert _cpu_quantity_to_vcpus("1500m") == 2  # 1.5 cores -> 2 usable vCPUs


def test_cpu_quantity_absent_or_bad() -> None:
    assert _cpu_quantity_to_vcpus(None) is None
    assert _cpu_quantity_to_vcpus("") is None
    assert _cpu_quantity_to_vcpus("garbage") is None


def test_pod_private_dns_name() -> None:
    assert (
        _pod_private_dns_name("orb-abc-0000", "default") == "orb-abc-0000.default.pod.cluster.local"
    )
    assert _pod_private_dns_name("", "default") is None
    assert _pod_private_dns_name("p", "") is None


def _pod(name: str = "orb-abc-0000", namespace: str = "default") -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels={}),
        status=SimpleNamespace(
            phase="Running",
            pod_ip="10.0.0.5",
            host_ip="10.0.1.1",
            start_time=None,
            conditions=[],
            container_statuses=[],
        ),
        spec=SimpleNamespace(node_name="node-a", containers=[SimpleNamespace(image="nginx")]),
    )


def test_instance_dict_has_private_dns_name_without_node_cache() -> None:
    d = instance_dict_for_pod(_pod(), "default", provider_api="Pod")
    assert d["private_dns_name"] == "orb-abc-0000.default.pod.cluster.local"
    assert d["public_dns_name"] is None
    assert d["provider_data"]["private_dns_name"] == "orb-abc-0000.default.pod.cluster.local"
    # No node cache → no vcpus key.
    assert "vcpus" not in d["provider_data"]


def test_instance_dict_has_vcpus_with_node_cache() -> None:
    node_state = SimpleNamespace(
        instance_type="m5.2xlarge",
        capacity_type="ondemand",
        zone="eu-west-1a",
        region="eu-west-1",
        cpu_capacity="8",
    )
    cache = SimpleNamespace(get=lambda _n: node_state)
    d = instance_dict_for_pod(_pod(), "default", provider_api="Pod", node_state_cache=cache)
    assert d["provider_data"]["vcpus"] == 8
    assert d["instance_type"] == "m5.2xlarge"
    assert d["provider_data"]["private_dns_name"].endswith(".default.pod.cluster.local")
