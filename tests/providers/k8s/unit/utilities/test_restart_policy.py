"""Tests for configurable ``restartPolicy`` resolution across workload kinds.

Covers the ``resolve_restart_policy`` helper, per-template and per-config
precedence, per-kind validity constraints, and the Deployment/StatefulSet
"always Always" rule.
"""

from __future__ import annotations

import uuid

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.k8s.exceptions.k8s_exceptions import K8sError
from orb.providers.k8s.utilities.deployment_spec import build_deployment_spec
from orb.providers.k8s.utilities.job_spec import build_job_spec
from orb.providers.k8s.utilities.pod_spec import build_pod_spec, resolve_restart_policy
from orb.providers.k8s.utilities.statefulset_spec import build_statefulset_spec

_ANY = frozenset({"Always", "OnFailure", "Never"})
_JOB = frozenset({"Never", "OnFailure"})


def _request(count: int = 1) -> Request:
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="tpl-1",
        requested_count=count,
    )


def _template(provider_api: str = "Pod", **fields) -> K8sTemplate:
    return K8sTemplate(
        template_id="tpl-1",
        provider_api=provider_api,
        image_id="registry.k8s.io/pause:3.9",
        max_instances=4,
        **fields,
    )


# ---------------------------------------------------------------------------
# resolve_restart_policy — resolution order + validation
# ---------------------------------------------------------------------------


def test_template_value_wins() -> None:
    tpl = _template(restart_policy="OnFailure")
    cfg = K8sProviderConfig(default_restart_policy="Never")
    assert (
        resolve_restart_policy(tpl, config=cfg, kind_default="Never", allowed_values=_ANY)
        == "OnFailure"
    )


def test_config_default_used_when_template_unset() -> None:
    tpl = _template()
    cfg = K8sProviderConfig(default_restart_policy="OnFailure")
    assert (
        resolve_restart_policy(tpl, config=cfg, kind_default="Never", allowed_values=_ANY)
        == "OnFailure"
    )


def test_kind_default_when_nothing_set() -> None:
    tpl = _template()
    assert (
        resolve_restart_policy(tpl, config=None, kind_default="Never", allowed_values=_ANY)
        == "Never"
    )


def test_rejects_value_outside_allowed_set() -> None:
    tpl = _template(restart_policy="Always")
    with pytest.raises(K8sError):
        resolve_restart_policy(tpl, config=None, kind_default="Never", allowed_values=_JOB)


# ---------------------------------------------------------------------------
# K8sTemplate validator
# ---------------------------------------------------------------------------


def test_template_rejects_bogus_restart_policy() -> None:
    with pytest.raises(Exception):
        _template(restart_policy="Sometimes")


# ---------------------------------------------------------------------------
# Pod builder honours the resolved policy
# ---------------------------------------------------------------------------


def test_pod_spec_defaults_never() -> None:
    pod = build_pod_spec(
        _template(), _request(), pod_name="p-0", machine_id="m-0", namespace="default"
    )
    assert pod.spec.restart_policy == "Never"


def test_pod_spec_honours_template_override() -> None:
    pod = build_pod_spec(
        _template(restart_policy="OnFailure"),
        _request(),
        pod_name="p-0",
        machine_id="m-0",
        namespace="default",
    )
    assert pod.spec.restart_policy == "OnFailure"


def test_pod_spec_honours_config_default() -> None:
    cfg = K8sProviderConfig(default_restart_policy="OnFailure")
    pod = build_pod_spec(
        _template(), _request(), pod_name="p-0", machine_id="m-0", namespace="default", config=cfg
    )
    assert pod.spec.restart_policy == "OnFailure"


# ---------------------------------------------------------------------------
# Job builder — Never/OnFailure only
# ---------------------------------------------------------------------------


def test_job_rejects_always() -> None:
    with pytest.raises(K8sError):
        build_job_spec(
            _template(provider_api="Job", restart_policy="Always"),
            _request(),
            job_name="j-0",
            namespace="default",
            parallelism=1,
        )


def test_job_allows_onfailure() -> None:
    job = build_job_spec(
        _template(provider_api="Job", restart_policy="OnFailure"),
        _request(),
        job_name="j-0",
        namespace="default",
        parallelism=1,
    )
    assert job.spec.template.spec.restart_policy == "OnFailure"


def test_job_rejects_config_default_always() -> None:
    """A config-level default_restart_policy=Always must still be rejected for Jobs."""
    cfg = K8sProviderConfig(default_restart_policy="Always")
    with pytest.raises(K8sError):
        build_job_spec(
            _template(provider_api="Job"),
            _request(),
            job_name="j-0",
            namespace="default",
            parallelism=1,
            config=cfg,
        )


def test_config_rejects_bogus_default_restart_policy() -> None:
    """K8sProviderConfig rejects an invalid default_restart_policy at load time."""
    with pytest.raises(Exception):
        K8sProviderConfig(default_restart_policy="sometimes")


# ---------------------------------------------------------------------------
# Deployment / StatefulSet always use Always (template value ignored w/ warning)
# ---------------------------------------------------------------------------


def test_deployment_always_ignores_template_override() -> None:
    dep = build_deployment_spec(
        _template(provider_api="Deployment", restart_policy="Never"),
        _request(),
        deployment_name="d-0",
        namespace="default",
        replicas=1,
    )
    assert dep.spec.template.spec.restart_policy == "Always"


def test_statefulset_always_ignores_template_override() -> None:
    sts = build_statefulset_spec(
        _template(provider_api="StatefulSet", restart_policy="OnFailure"),
        _request(),
        statefulset_name="s-0",
        namespace="default",
        replicas=1,
    )
    assert sts.spec.template.spec.restart_policy == "Always"
