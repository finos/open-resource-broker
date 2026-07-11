"""Unit tests for :mod:`orb.providers.k8s.utilities.deployment_spec`.

Covers Deployment-name generation, label/selector inheritance, replica
counts, pod-template invariants, and provider-config defaults.  The
kubernetes SDK is imported lazily by ``build_deployment_spec`` — the
tests exercise that import path so they run only when the
``[kubernetes]`` extra is available.
"""

from __future__ import annotations

import uuid

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.utilities.deployment_spec import (
    build_deployment_spec,
    make_deployment_name,
)
from orb.providers.k8s.utilities.pod_spec import LEGACY_REQUEST_ID_LABEL


def _build_request(*, requested_count: int = 3) -> Request:
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Deployment",
        template_id="tpl-1",
        requested_count=requested_count,
    )


def _build_template(**k8s_fields) -> Template:
    """Build a :class:`K8sTemplate` for tests."""
    from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate

    image_id = k8s_fields.pop("container_image", None) or k8s_fields.pop(
        "image_id", "busybox:latest"
    )
    return K8sTemplate(
        template_id="tpl-1",
        provider_api="Deployment",
        image_id=image_id,
        max_instances=5,
        **k8s_fields,
    )


# ---------------------------------------------------------------------------
# make_deployment_name
# ---------------------------------------------------------------------------


def test_make_deployment_name_uses_request_prefix() -> None:
    name = make_deployment_name("abcdef1234567890")
    assert name == "orb-abcdef12"


def test_make_deployment_name_handles_short_request_id() -> None:
    assert make_deployment_name("xy") == "orb-xy"


def test_make_deployment_name_handles_empty_request_id() -> None:
    assert make_deployment_name("") == "orb-unknown"


# ---------------------------------------------------------------------------
# build_deployment_spec
# ---------------------------------------------------------------------------


def test_build_deployment_spec_replicas_and_selector() -> None:
    request = _build_request(requested_count=3)
    template = _build_template()

    dep = build_deployment_spec(
        template,
        request,
        deployment_name="orb-aaaa",
        namespace="orb-test",
        replicas=3,
    )

    assert dep.api_version == "apps/v1"
    assert dep.kind == "Deployment"
    assert dep.metadata is not None
    assert dep.metadata.name == "orb-aaaa"
    assert dep.metadata.namespace == "orb-test"
    assert dep.spec is not None
    assert dep.spec.replicas == 3

    # Selector points at the request-id label so the Deployment owns
    # exactly the pods that ORB knows about.
    assert dep.spec.selector is not None
    match_labels = dep.spec.selector.match_labels or {}
    assert match_labels["orb.io/request-id"] == str(request.request_id)
    assert match_labels["orb.io/provider-api"] == "Deployment"


def test_build_deployment_spec_pod_template_labels_and_restart_policy() -> None:
    request = _build_request()
    template = _build_template()

    dep = build_deployment_spec(
        template,
        request,
        deployment_name="orb-bbbb",
        namespace="default",
        replicas=2,
    )

    pod_template = dep.spec.template
    assert pod_template is not None
    assert pod_template.metadata is not None
    labels = pod_template.metadata.labels or {}
    assert labels["orb.io/managed"] == "true"
    assert labels["orb.io/request-id"] == str(request.request_id)
    assert labels["orb.io/provider-api"] == "Deployment"
    assert labels["orb.io/template-id"] == "tpl-1"
    # Deployment pods MUST use restartPolicy Always — the controller
    # contract requires it.
    assert pod_template.spec is not None
    assert pod_template.spec.restart_policy == "Always"
    # Legacy label is emitted by default.
    assert LEGACY_REQUEST_ID_LABEL in labels


def test_build_deployment_spec_pod_template_omits_machine_id_label() -> None:
    """The Deployment pod-template label set MUST NOT include ``machine-id``.

    Pod names are assigned by the controller; a fixed ``machine-id``
    label across N replicas would point all pods at the same identity.
    The Deployment object itself also drops the label so it isn't
    conflated with a real machine.
    """
    request = _build_request()
    template = _build_template()
    dep = build_deployment_spec(
        template,
        request,
        deployment_name="orb-cccc",
        namespace="default",
        replicas=1,
    )

    assert dep.metadata is not None
    deployment_labels = dep.metadata.labels or {}
    assert "orb.io/machine-id" not in deployment_labels

    pod_template = dep.spec.template
    assert pod_template is not None
    assert pod_template.metadata is not None
    pod_labels = pod_template.metadata.labels or {}
    assert "orb.io/machine-id" not in pod_labels


def test_build_deployment_spec_resource_requests_and_limits() -> None:
    request = _build_request()
    template = _build_template(
        container_image="alpine:3.20",
        resource_requests={"cpu": "100m", "memory": "128Mi"},
        resource_limits={"cpu": "500m", "memory": "256Mi"},
        command=["sh", "-c", "echo hi"],
    )
    dep = build_deployment_spec(
        template,
        request,
        deployment_name="orb-dddd",
        namespace="orb",
        replicas=2,
    )
    container = dep.spec.template.spec.containers[0]
    assert container.image == "alpine:3.20"
    assert container.command == ["sh", "-c", "echo hi"]
    assert container.resources is not None
    assert container.resources.requests == {"cpu": "100m", "memory": "128Mi"}
    assert container.resources.limits == {"cpu": "500m", "memory": "256Mi"}


def test_build_deployment_spec_applies_provider_config_defaults() -> None:
    request = _build_request()
    template = _build_template()
    config = K8sProviderConfig(
        namespace="orb",
        default_node_selector={"role": "compute"},
        default_tolerations=[
            {"key": "dedicated", "operator": "Equal", "value": "orb", "effect": "NoSchedule"},
        ],
        default_image_pull_secret="my-pull-secret",
    )
    dep = build_deployment_spec(
        template,
        request,
        deployment_name="orb-eeee",
        namespace="orb",
        replicas=1,
        config=config,
    )
    pod_spec = dep.spec.template.spec
    assert pod_spec.node_selector == {"role": "compute"}
    assert pod_spec.tolerations is not None
    assert len(pod_spec.tolerations) == 1
    assert pod_spec.tolerations[0].key == "dedicated"
    assert pod_spec.image_pull_secrets is not None
    assert pod_spec.image_pull_secrets[0].name == "my-pull-secret"


def test_build_deployment_spec_respects_custom_label_prefix() -> None:
    request = _build_request()
    template = _build_template()
    config = K8sProviderConfig(label_prefix="example.com", emit_legacy_labels=False)
    dep = build_deployment_spec(
        template,
        request,
        deployment_name="orb-ffff",
        namespace="default",
        replicas=1,
        config=config,
    )

    deployment_labels = dep.metadata.labels or {}
    assert "example.com/managed" in deployment_labels
    assert "orb.io/managed" not in deployment_labels
    assert LEGACY_REQUEST_ID_LABEL not in deployment_labels

    # Selector picks up the custom prefix too.
    selector = dep.spec.selector.match_labels or {}
    assert "example.com/request-id" in selector
    assert "orb.io/request-id" not in selector


def test_build_deployment_spec_requires_image() -> None:
    request = _build_request()
    template = Template(
        template_id="tpl-no-image",
        provider_type="k8s",
        provider_api="Deployment",
        max_instances=1,
    )
    with pytest.raises(ValueError, match="container image"):
        build_deployment_spec(
            template,
            request,
            deployment_name="orb-gggg",
            namespace="default",
            replicas=1,
        )


def test_build_deployment_spec_rejects_negative_replicas() -> None:
    request = _build_request()
    template = _build_template()
    with pytest.raises(ValueError, match="replicas must be >= 0"):
        build_deployment_spec(
            template,
            request,
            deployment_name="orb-hhhh",
            namespace="default",
            replicas=-1,
        )


def test_build_deployment_spec_zero_replicas_allowed() -> None:
    """Zero replicas is valid — used by the full-release path."""
    request = _build_request()
    template = _build_template()
    dep = build_deployment_spec(
        template,
        request,
        deployment_name="orb-iiii",
        namespace="default",
        replicas=0,
    )
    assert dep.spec.replicas == 0
