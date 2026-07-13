"""Tests for AWS-parity fields in the k8s instance dict.

Covers private_dns_name (dashed-IP form for CoreDNS resolution), provider_data.vcpus
(from the node cache), the CPU-quantity parser, and init-container failure surfacing.
"""

from __future__ import annotations

from types import SimpleNamespace

from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
    _cpu_quantity_to_vcpus,
    _pod_private_dns_name,
    instance_dict_for_pod,
    instance_dict_for_state,
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


# ---------------------------------------------------------------------------
# _pod_private_dns_name — dashed-IP form
# ---------------------------------------------------------------------------


def test_pod_private_dns_name_uses_dashed_ip() -> None:
    """CoreDNS resolves pod A records on the dashed-IP form, not pod-name form."""
    assert _pod_private_dns_name("10.0.0.5", "default") == "10-0-0-5.default.pod.cluster.local"


def test_pod_private_dns_name_none_when_no_ip() -> None:
    assert _pod_private_dns_name(None, "default") is None
    assert _pod_private_dns_name("", "default") is None


def test_pod_private_dns_name_none_when_no_namespace() -> None:
    assert _pod_private_dns_name("10.0.0.5", "") is None
    assert _pod_private_dns_name("10.0.0.5", None) is None  # type: ignore[arg-type]


def test_pod_private_dns_name_ipv4_dotted() -> None:
    """All four octets must be dashed."""
    assert _pod_private_dns_name("192.168.1.200", "prod") == "192-168-1-200.prod.pod.cluster.local"


# ---------------------------------------------------------------------------
# instance_dict_for_pod — private_dns_name reflects pod_ip
# ---------------------------------------------------------------------------


def _pod(
    name: str = "orb-abc-0000",
    namespace: str = "default",
    pod_ip: str = "10.0.0.5",
    init_container_statuses: list[object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels={}),
        status=SimpleNamespace(
            phase="Running",
            pod_ip=pod_ip,
            host_ip="10.0.1.1",
            start_time=None,
            conditions=[],
            container_statuses=[],
            init_container_statuses=init_container_statuses or [],
        ),
        spec=SimpleNamespace(node_name="node-a", containers=[SimpleNamespace(image="nginx")]),
    )


def test_instance_dict_has_private_dns_name_dashed_ip() -> None:
    """instance_dict_for_pod must emit the dashed-IP form so CoreDNS resolves it."""
    d = instance_dict_for_pod(_pod(pod_ip="10.0.0.5"), "default", provider_api="Pod")
    assert d["private_dns_name"] == "10-0-0-5.default.pod.cluster.local"
    assert d["public_dns_name"] is None
    assert d["provider_data"]["private_dns_name"] == "10-0-0-5.default.pod.cluster.local"
    # No node cache → no vcpus key.
    assert "vcpus" not in d["provider_data"]


def test_instance_dict_private_dns_name_none_when_no_ip() -> None:
    """When the pod has no IP yet (pending), private_dns_name must be None."""
    d = instance_dict_for_pod(_pod(pod_ip=""), "default", provider_api="Pod")
    assert d["private_dns_name"] is None
    assert d["provider_data"]["private_dns_name"] is None


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
    # dashed-IP form is still present
    assert d["provider_data"]["private_dns_name"] == "10-0-0-5.default.pod.cluster.local"


# ---------------------------------------------------------------------------
# instance_dict_for_state — private_dns_name from state.pod_ip
# ---------------------------------------------------------------------------


def _pod_state(
    pod_ip: str | None = "10.0.0.5",
) -> SimpleNamespace:
    """Return a minimal PodState-like object for cache-path tests."""
    return SimpleNamespace(
        request_id="req-1",
        pod_name="orb-abc-0000",
        namespace="default",
        status="running",
        phase="Running",
        ready=True,
        pod_ip=pod_ip,
        host_ip="10.0.1.1",
        node_name="node-a",
        status_reason=None,
        start_time=None,
        labels={},
        disrupted_reason=None,
        disrupted_message=None,
        restart_count=0,
        image_id="nginx",
    )


def test_instance_dict_for_state_dashed_ip_dns_name() -> None:
    """Cache-fed path must also emit the dashed-IP DNS form."""
    d = instance_dict_for_state(_pod_state(pod_ip="10.0.0.5"), provider_api="Pod")
    assert d["private_dns_name"] == "10-0-0-5.default.pod.cluster.local"
    assert d["provider_data"]["private_dns_name"] == "10-0-0-5.default.pod.cluster.local"


def test_instance_dict_for_state_none_when_no_ip() -> None:
    """Cache-fed path returns None when pod_ip is absent (not yet scheduled)."""
    d = instance_dict_for_state(_pod_state(pod_ip=None), provider_api="Pod")
    assert d["private_dns_name"] is None
    assert d["provider_data"]["private_dns_name"] is None


# ---------------------------------------------------------------------------
# Init-container failure surfacing (Fix 6)
# ---------------------------------------------------------------------------


def _waiting_cs(reason: str) -> SimpleNamespace:
    """Return a container-status stub stuck in a waiting state."""
    return SimpleNamespace(
        restart_count=0,
        state=SimpleNamespace(
            terminated=None,
            waiting=SimpleNamespace(reason=reason),
        ),
        last_state=SimpleNamespace(terminated=None),
    )


def test_init_container_image_pull_backoff_surfaces_as_failed() -> None:
    """Pod stuck Init:ImagePullBackOff must become failed, not pending."""
    init_cs = _waiting_cs("ImagePullBackOff")
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="orb-init-pod", labels={}),
        status=SimpleNamespace(
            phase="Pending",
            pod_ip=None,
            host_ip=None,
            start_time=None,
            conditions=[],
            container_statuses=[],  # main containers not yet started
            init_container_statuses=[init_cs],
        ),
        spec=SimpleNamespace(
            node_name="node-a",
            containers=[SimpleNamespace(image="app:v1")],
            restart_policy=None,
        ),
    )
    d = instance_dict_for_pod(pod, "default", provider_api="Pod")
    assert d["status"] == "failed", f"Expected failed, got {d['status']!r}"
    assert d["status_reason"] == "ImagePullBackOff"


def test_init_container_err_image_pull_surfaces_as_failed() -> None:
    """ErrImagePull on an init container must also escalate to failed."""
    init_cs = _waiting_cs("ErrImagePull")
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="orb-init-pod-2", labels={}),
        status=SimpleNamespace(
            phase="Pending",
            pod_ip=None,
            host_ip=None,
            start_time=None,
            conditions=[],
            container_statuses=[],
            init_container_statuses=[init_cs],
        ),
        spec=SimpleNamespace(
            node_name="node-a",
            containers=[SimpleNamespace(image="app:v1")],
            restart_policy=None,
        ),
    )
    d = instance_dict_for_pod(pod, "default", provider_api="Pod")
    assert d["status"] == "failed"
    assert d["status_reason"] == "ErrImagePull"


def test_non_fatal_init_container_waiting_stays_pending() -> None:
    """A non-fatal init-container waiting reason must not escalate the pod."""
    init_cs = _waiting_cs("PodInitializing")  # non-fatal — init container running
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="orb-init-pod-3", labels={}),
        status=SimpleNamespace(
            phase="Pending",
            pod_ip=None,
            host_ip=None,
            start_time=None,
            conditions=[],
            container_statuses=[],
            init_container_statuses=[init_cs],
        ),
        spec=SimpleNamespace(
            node_name=None,
            containers=[SimpleNamespace(image="app:v1")],
            restart_policy=None,
        ),
    )
    d = instance_dict_for_pod(pod, "default", provider_api="Pod")
    assert d["status"] == "pending"
