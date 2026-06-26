"""Unit tests for volume_mounts, probes, security_context, priority_class_name,
termination_grace_period_seconds, and Job lifecycle fields across all four
spec builders.

Each spec builder delegates to shared helpers in
:mod:`orb.providers.k8s.utilities.pod_spec`, so the volume-mount / probe /
security-context assertions exercise the full path from K8sTemplate → builder
helper → V1Container / V1PodSpec.
"""

from __future__ import annotations

import uuid

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.k8s.domain.template.k8s_template import (
    K8sProbe,
    K8sSecurityContext,
    K8sTemplate,
)
from orb.providers.k8s.utilities.deployment_spec import build_deployment_spec
from orb.providers.k8s.utilities.job_spec import build_job_spec
from orb.providers.k8s.utilities.pod_spec import build_pod_spec
from orb.providers.k8s.utilities.statefulset_spec import build_statefulset_spec

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_request(provider_api: str = "Pod") -> Request:
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api=provider_api,
        template_id="tpl-1",
        requested_count=2,
    )


def _make_template(**kwargs) -> K8sTemplate:
    kwargs.setdefault("image_id", "busybox:latest")
    return K8sTemplate(template_id="tpl-1", **kwargs)


# ---------------------------------------------------------------------------
# volume_mounts — fix for silent-discard defect
# ---------------------------------------------------------------------------


def test_build_pod_spec_volume_mounts_present_on_container() -> None:
    """volume_mounts must be wired onto the container — was silently dropped."""
    template = _make_template(
        volume_mounts=[
            {"name": "data", "mountPath": "/data"},
            {"name": "config", "mountPath": "/etc/config", "readOnly": True},
        ],
        volumes=[{"name": "data", "source": {"emptyDir": {}}}],
    )
    pod = build_pod_spec(
        template,
        _make_request("Pod"),
        pod_name="orb-aaaa-0000",
        machine_id="orb-aaaa-0000",
        namespace="default",
    )
    container = pod.spec.containers[0]
    assert container.volume_mounts is not None
    assert len(container.volume_mounts) == 2
    names = {m.name for m in container.volume_mounts}
    assert names == {"data", "config"}
    # Verify mount_path is wired through
    data_mount = next(m for m in container.volume_mounts if m.name == "data")
    assert data_mount.mount_path == "/data"


def test_build_deployment_spec_volume_mounts_present_on_container() -> None:
    template = _make_template(
        volume_mounts=[{"name": "logs", "mountPath": "/var/log"}],
    )
    dep = build_deployment_spec(
        template,
        _make_request("Deployment"),
        deployment_name="orb-bbbb",
        namespace="default",
        replicas=1,
    )
    container = dep.spec.template.spec.containers[0]
    assert container.volume_mounts is not None
    assert len(container.volume_mounts) == 1
    assert container.volume_mounts[0].name == "logs"
    assert container.volume_mounts[0].mount_path == "/var/log"


def test_build_statefulset_spec_volume_mounts_present_on_container() -> None:
    template = _make_template(
        volume_mounts=[{"name": "state", "mountPath": "/state"}],
    )
    sts = build_statefulset_spec(
        template,
        _make_request("StatefulSet"),
        statefulset_name="orb-cccc",
        namespace="default",
        replicas=1,
    )
    container = sts.spec.template.spec.containers[0]
    assert container.volume_mounts is not None
    assert container.volume_mounts[0].name == "state"


def test_build_job_spec_volume_mounts_present_on_container() -> None:
    template = _make_template(
        volume_mounts=[{"name": "scratch", "mountPath": "/scratch"}],
    )
    job = build_job_spec(
        template,
        _make_request("Job"),
        job_name="orb-dddd",
        namespace="default",
        parallelism=1,
    )
    container = job.spec.template.spec.containers[0]
    assert container.volume_mounts is not None
    assert container.volume_mounts[0].name == "scratch"


def test_volume_mounts_none_when_not_set() -> None:
    """When volume_mounts is absent the container field must be None, not []."""
    pod = build_pod_spec(
        _make_template(),
        _make_request("Pod"),
        pod_name="orb-eeee-0000",
        machine_id="orb-eeee-0000",
        namespace="default",
    )
    assert pod.spec.containers[0].volume_mounts is None


# ---------------------------------------------------------------------------
# K8sProbe model — construction and serialisation
# ---------------------------------------------------------------------------


def test_k8s_probe_http_get_round_trip() -> None:
    probe = K8sProbe(
        http_get={"path": "/healthz", "port": 8080},
        initial_delay_seconds=5,
        period_seconds=10,
    )
    d = probe.to_api_dict()
    assert d["httpGet"] == {"path": "/healthz", "port": 8080}
    assert d["initialDelaySeconds"] == 5
    assert d["periodSeconds"] == 10


def test_k8s_probe_exec_round_trip() -> None:
    probe = K8sProbe(exec={"command": ["cat", "/tmp/ready"]}, period_seconds=5)
    d = probe.to_api_dict()
    assert d["exec"] == {"command": ["cat", "/tmp/ready"]}
    assert d["periodSeconds"] == 5
    assert "httpGet" not in d


def test_k8s_probe_dict_coercion_via_field_validator() -> None:
    template = _make_template(
        readiness_probe={"httpGet": {"path": "/ready", "port": 8080}, "periodSeconds": 10},
    )
    assert isinstance(template.readiness_probe, K8sProbe)
    assert template.readiness_probe.http_get == {"path": "/ready", "port": 8080}


# ---------------------------------------------------------------------------
# readiness_probe / liveness_probe — wired to all spec builders
# ---------------------------------------------------------------------------


def test_build_pod_spec_readiness_probe_wired() -> None:
    template = _make_template(
        readiness_probe=K8sProbe(
            http_get={"path": "/ready", "port": 8080},
            initial_delay_seconds=3,
        ),
    )
    pod = build_pod_spec(
        template,
        _make_request("Pod"),
        pod_name="orb-ffff-0000",
        machine_id="orb-ffff-0000",
        namespace="default",
    )
    container = pod.spec.containers[0]
    assert container.readiness_probe is not None
    # The SDK object exposes http_get (snake_case) with path / port
    assert container.readiness_probe.http_get is not None
    assert container.readiness_probe.initial_delay_seconds == 3


def test_build_pod_spec_liveness_probe_wired() -> None:
    template = _make_template(
        liveness_probe=K8sProbe(
            exec={"command": ["test", "-f", "/tmp/alive"]},
            period_seconds=15,
            failure_threshold=3,
        ),
    )
    pod = build_pod_spec(
        template,
        _make_request("Pod"),
        pod_name="orb-gggg-0000",
        machine_id="orb-gggg-0000",
        namespace="default",
    )
    container = pod.spec.containers[0]
    assert container.liveness_probe is not None
    assert container.liveness_probe.period_seconds == 15
    assert container.liveness_probe.failure_threshold == 3


def test_build_deployment_spec_probes_wired() -> None:
    template = _make_template(
        readiness_probe=K8sProbe(http_get={"path": "/healthz", "port": 9000}, period_seconds=5),
        liveness_probe=K8sProbe(exec={"command": ["ls"]}, period_seconds=30),
    )
    dep = build_deployment_spec(
        template,
        _make_request("Deployment"),
        deployment_name="orb-hhhh",
        namespace="default",
        replicas=1,
    )
    container = dep.spec.template.spec.containers[0]
    assert container.readiness_probe is not None
    assert container.liveness_probe is not None


def test_build_job_spec_probes_wired() -> None:
    template = _make_template(
        liveness_probe=K8sProbe(
            tcp_socket={"port": 6379}, initial_delay_seconds=10, period_seconds=5
        ),
    )
    job = build_job_spec(
        template,
        _make_request("Job"),
        job_name="orb-iiii",
        namespace="default",
        parallelism=1,
    )
    container = job.spec.template.spec.containers[0]
    assert container.liveness_probe is not None


def test_probes_absent_when_not_set() -> None:
    pod = build_pod_spec(
        _make_template(),
        _make_request("Pod"),
        pod_name="orb-jjjj-0000",
        machine_id="orb-jjjj-0000",
        namespace="default",
    )
    container = pod.spec.containers[0]
    assert container.readiness_probe is None
    assert container.liveness_probe is None


# ---------------------------------------------------------------------------
# K8sSecurityContext model — construction and serialisation
# ---------------------------------------------------------------------------


def test_k8s_security_context_round_trip() -> None:
    sc = K8sSecurityContext(
        run_as_user=1000,
        run_as_group=2000,
        fs_group=3000,
        run_as_non_root=True,
    )
    d = sc.to_api_dict()
    assert d["runAsUser"] == 1000
    assert d["runAsGroup"] == 2000
    assert d["fsGroup"] == 3000
    assert d["runAsNonRoot"] is True
    assert "seccompProfile" not in d


def test_k8s_security_context_dict_coercion() -> None:
    template = _make_template(
        security_context={"runAsUser": 65534, "runAsNonRoot": True},
    )
    assert isinstance(template.security_context, K8sSecurityContext)
    assert template.security_context.run_as_user == 65534
    assert template.security_context.run_as_non_root is True


# ---------------------------------------------------------------------------
# security_context — wired to pod spec
# ---------------------------------------------------------------------------


def test_build_pod_spec_security_context_wired() -> None:
    template = _make_template(
        security_context=K8sSecurityContext(
            run_as_non_root=True,
            run_as_user=1000,
            fs_group=2000,
        ),
    )
    pod = build_pod_spec(
        template,
        _make_request("Pod"),
        pod_name="orb-kkkk-0000",
        machine_id="orb-kkkk-0000",
        namespace="default",
    )
    sc = pod.spec.security_context
    assert sc is not None
    assert sc.run_as_non_root is True
    assert sc.run_as_user == 1000
    assert sc.fs_group == 2000


def test_build_deployment_spec_security_context_wired() -> None:
    template = _make_template(
        security_context=K8sSecurityContext(run_as_user=500),
    )
    dep = build_deployment_spec(
        template,
        _make_request("Deployment"),
        deployment_name="orb-llll",
        namespace="default",
        replicas=1,
    )
    sc = dep.spec.template.spec.security_context
    assert sc is not None
    assert sc.run_as_user == 500


def test_build_job_spec_security_context_wired() -> None:
    template = _make_template(
        security_context=K8sSecurityContext(fs_group=999),
    )
    job = build_job_spec(
        template,
        _make_request("Job"),
        job_name="orb-mmmm",
        namespace="default",
        parallelism=1,
    )
    sc = job.spec.template.spec.security_context
    assert sc is not None
    assert sc.fs_group == 999


def test_security_context_absent_when_not_set() -> None:
    pod = build_pod_spec(
        _make_template(),
        _make_request("Pod"),
        pod_name="orb-nnnn-0000",
        machine_id="orb-nnnn-0000",
        namespace="default",
    )
    assert pod.spec.security_context is None


# ---------------------------------------------------------------------------
# priority_class_name — wired to pod spec
# ---------------------------------------------------------------------------


def test_build_pod_spec_priority_class_name_wired() -> None:
    template = _make_template(priority_class_name="high-priority")
    pod = build_pod_spec(
        template,
        _make_request("Pod"),
        pod_name="orb-oooo-0000",
        machine_id="orb-oooo-0000",
        namespace="default",
    )
    assert pod.spec.priority_class_name == "high-priority"


def test_build_deployment_spec_priority_class_name_wired() -> None:
    template = _make_template(priority_class_name="best-effort")
    dep = build_deployment_spec(
        template,
        _make_request("Deployment"),
        deployment_name="orb-pppp",
        namespace="default",
        replicas=1,
    )
    assert dep.spec.template.spec.priority_class_name == "best-effort"


def test_build_job_spec_priority_class_name_wired() -> None:
    template = _make_template(priority_class_name="batch-low")
    job = build_job_spec(
        template,
        _make_request("Job"),
        job_name="orb-qqqq",
        namespace="default",
        parallelism=1,
    )
    assert job.spec.template.spec.priority_class_name == "batch-low"


def test_priority_class_name_absent_when_not_set() -> None:
    pod = build_pod_spec(
        _make_template(),
        _make_request("Pod"),
        pod_name="orb-rrrr-0000",
        machine_id="orb-rrrr-0000",
        namespace="default",
    )
    assert pod.spec.priority_class_name is None


# ---------------------------------------------------------------------------
# termination_grace_period_seconds — wired to pod spec
# ---------------------------------------------------------------------------


def test_build_pod_spec_termination_grace_period_wired() -> None:
    template = _make_template(termination_grace_period_seconds=120)
    pod = build_pod_spec(
        template,
        _make_request("Pod"),
        pod_name="orb-ssss-0000",
        machine_id="orb-ssss-0000",
        namespace="default",
    )
    assert pod.spec.termination_grace_period_seconds == 120


def test_build_pod_spec_termination_grace_period_zero_allowed() -> None:
    """Zero termination grace is valid — used for immediate shutdown."""
    template = _make_template(termination_grace_period_seconds=0)
    pod = build_pod_spec(
        template,
        _make_request("Pod"),
        pod_name="orb-tttt-0000",
        machine_id="orb-tttt-0000",
        namespace="default",
    )
    assert pod.spec.termination_grace_period_seconds == 0


def test_termination_grace_period_absent_when_not_set() -> None:
    pod = build_pod_spec(
        _make_template(),
        _make_request("Pod"),
        pod_name="orb-uuuu-0000",
        machine_id="orb-uuuu-0000",
        namespace="default",
    )
    assert pod.spec.termination_grace_period_seconds is None


# ---------------------------------------------------------------------------
# Job lifecycle — ttl_seconds_after_finished / active_deadline_seconds
# ---------------------------------------------------------------------------


def test_build_job_spec_ttl_seconds_after_finished_wired() -> None:
    template = _make_template(ttl_seconds_after_finished=3600)
    job = build_job_spec(
        template,
        _make_request("Job"),
        job_name="orb-vvvv",
        namespace="default",
        parallelism=1,
    )
    assert job.spec.ttl_seconds_after_finished == 3600


def test_build_job_spec_active_deadline_seconds_wired() -> None:
    template = _make_template(active_deadline_seconds=7200)
    job = build_job_spec(
        template,
        _make_request("Job"),
        job_name="orb-wwww",
        namespace="default",
        parallelism=1,
    )
    assert job.spec.active_deadline_seconds == 7200


def test_build_job_spec_both_lifecycle_fields_wired() -> None:
    template = _make_template(
        ttl_seconds_after_finished=300,
        active_deadline_seconds=1800,
    )
    job = build_job_spec(
        template,
        _make_request("Job"),
        job_name="orb-xxxx",
        namespace="default",
        parallelism=2,
    )
    assert job.spec.ttl_seconds_after_finished == 300
    assert job.spec.active_deadline_seconds == 1800


def test_build_job_spec_lifecycle_absent_when_not_set() -> None:
    """TTL and deadline must not appear at all when not set."""
    job = build_job_spec(
        _make_template(),
        _make_request("Job"),
        job_name="orb-yyyy",
        namespace="default",
        parallelism=1,
    )
    assert job.spec.ttl_seconds_after_finished is None
    assert job.spec.active_deadline_seconds is None


# ---------------------------------------------------------------------------
# Field validators on K8sTemplate
# ---------------------------------------------------------------------------


def test_termination_grace_period_negative_rejected() -> None:
    with pytest.raises(Exception):
        _make_template(termination_grace_period_seconds=-1)


def test_ttl_seconds_after_finished_negative_rejected() -> None:
    with pytest.raises(Exception):
        _make_template(ttl_seconds_after_finished=-1)


def test_active_deadline_seconds_zero_rejected() -> None:
    with pytest.raises(Exception):
        _make_template(active_deadline_seconds=0)


def test_active_deadline_seconds_positive_accepted() -> None:
    t = _make_template(active_deadline_seconds=1)
    assert t.active_deadline_seconds == 1


# ---------------------------------------------------------------------------
# Statefulset spec — ensure all new fields wired symmetrically
# ---------------------------------------------------------------------------


def test_build_statefulset_spec_all_new_fields() -> None:
    template = _make_template(
        volume_mounts=[{"name": "pvc", "mountPath": "/data"}],
        readiness_probe=K8sProbe(http_get={"path": "/ready", "port": 8080}, period_seconds=5),
        security_context=K8sSecurityContext(run_as_user=1001),
        priority_class_name="statefulset-priority",
        termination_grace_period_seconds=60,
    )
    sts = build_statefulset_spec(
        template,
        _make_request("StatefulSet"),
        statefulset_name="orb-zzzz",
        namespace="default",
        replicas=2,
    )
    pod_spec = sts.spec.template.spec
    container = pod_spec.containers[0]

    assert container.volume_mounts is not None
    assert container.volume_mounts[0].name == "pvc"
    assert container.readiness_probe is not None
    assert pod_spec.security_context is not None
    assert pod_spec.security_context.run_as_user == 1001
    assert pod_spec.priority_class_name == "statefulset-priority"
    assert pod_spec.termination_grace_period_seconds == 60
