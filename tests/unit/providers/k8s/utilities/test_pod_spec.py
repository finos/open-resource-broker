"""Unit tests for :mod:`orb.providers.k8s.utilities.pod_spec`.

Covers pod-name generation, label injection, and pod-spec assembly.  The
kubernetes SDK is only used by ``build_pod_spec`` — the helpers are
otherwise pure.  Tests run without any cluster.
"""

from __future__ import annotations

import uuid

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.utilities.pod_spec import (
    LEGACY_REQUEST_ID_LABEL,
    build_pod_labels,
    build_pod_spec,
    make_pod_name,
    request_id_label_selector,
)


def _build_request(*, requested_count: int = 1) -> Request:
    """Construct a minimal :class:`Request` suitable for handler tests."""
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="tpl-1",
        requested_count=requested_count,
    )


def _build_template(**k8s_fields) -> Template:
    """Build a :class:`K8sTemplate` for tests.

    The k8s-specific fields are passed as flat kwargs and forwarded
    directly to the typed template constructor.  ``container_image`` is
    accepted as a legacy alias for ``image_id`` to keep existing tests
    succinct.
    """
    from orb.providers.k8s.domain.template.k8s_template import K8sTemplate

    image_id = k8s_fields.pop("container_image", None) or k8s_fields.pop(
        "image_id", "busybox:latest"
    )
    return K8sTemplate(
        template_id="tpl-1",
        provider_api="Pod",
        image_id=image_id,
        max_instances=2,
        **k8s_fields,
    )


# ---------------------------------------------------------------------------
# make_pod_name
# ---------------------------------------------------------------------------


def test_make_pod_name_pads_sequence() -> None:
    # Hyphens are stripped and up to 20 chars are taken from the request_id.
    name = make_pod_name("abcdef1234567890", 7)
    assert name == "orb-abcdef1234567890-0007"
    assert len(name) <= 63


def test_make_pod_name_handles_short_request_id() -> None:
    name = make_pod_name("xy", 0)
    assert name == "orb-xy-0000"


def test_make_pod_name_handles_empty_request_id() -> None:
    name = make_pod_name("", 3)
    assert name == "orb-unknown-0003"


def test_make_pod_name_strips_uuid_hyphens() -> None:
    # UUID-formatted request_id: hyphens stripped before slicing.
    name = make_pod_name("550e8400-e29b-41d4-a716-446655440000", 1)
    # Stripped UUID = "550e8400e29b41d4a716446655440000" → first 20 chars = "550e8400e29b41d4a716"
    assert name == "orb-550e8400e29b41d4a716-0001"
    assert len(name) <= 63


def test_make_pod_name_collision_uniqueness() -> None:
    """1000 random UUIDs must all produce distinct pod names for seq=0."""
    names = {make_pod_name(str(uuid.uuid4()), 0) for _ in range(1000)}
    assert len(names) == 1000


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def test_build_pod_labels_includes_legacy_when_enabled() -> None:
    request = _build_request()
    labels = build_pod_labels(
        request,
        machine_id="orb-deadbeef-0000",
        provider_api="Pod",
        label_prefix="orb.io",
        emit_legacy_labels=True,
    )
    assert labels["orb.io/managed"] == "true"
    assert labels["orb.io/request-id"] == str(request.request_id)
    assert labels["orb.io/machine-id"] == "orb-deadbeef-0000"
    assert labels["orb.io/provider-api"] == "Pod"
    assert labels["orb.io/template-id"] == "tpl-1"
    assert labels[LEGACY_REQUEST_ID_LABEL] == str(request.request_id)


def test_build_pod_labels_omits_legacy_when_disabled() -> None:
    request = _build_request()
    labels = build_pod_labels(
        request,
        machine_id="orb-deadbeef-0000",
        emit_legacy_labels=False,
    )
    assert LEGACY_REQUEST_ID_LABEL not in labels


def test_build_pod_labels_respects_custom_prefix() -> None:
    request = _build_request()
    labels = build_pod_labels(
        request,
        machine_id="orb-x-0000",
        label_prefix="example.com",
    )
    assert "example.com/managed" in labels
    assert "orb.io/managed" not in labels


def test_request_id_label_selector_matches_label_prefix() -> None:
    request = _build_request()
    selector = request_id_label_selector(request, label_prefix="orb.io")
    assert selector == f"orb.io/request-id={request.request_id}"


# ---------------------------------------------------------------------------
# build_pod_spec
# ---------------------------------------------------------------------------


def test_build_pod_spec_minimal_template() -> None:
    request = _build_request()
    template = _build_template()
    pod = build_pod_spec(
        template,
        request,
        pod_name="orb-aaaa-0000",
        machine_id="orb-aaaa-0000",
        namespace="default",
    )
    assert pod.api_version == "v1"
    assert pod.kind == "Pod"
    assert pod.metadata is not None
    assert pod.metadata.name == "orb-aaaa-0000"
    assert pod.metadata.namespace == "default"
    assert pod.spec is not None
    assert pod.spec.restart_policy == "Never"
    assert pod.spec.containers is not None
    assert len(pod.spec.containers) == 1
    assert pod.spec.containers[0].image == "busybox:latest"
    assert pod.spec.containers[0].resources is None


def test_build_pod_spec_resource_requests_and_limits() -> None:
    request = _build_request()
    template = _build_template(
        container_image="alpine:3.20",
        resource_requests={"cpu": "100m", "memory": "128Mi"},
        resource_limits={"cpu": "500m", "memory": "256Mi"},
        command=["sh", "-c", "echo hi"],
    )
    pod = build_pod_spec(
        template,
        request,
        pod_name="orb-bbbb-0000",
        machine_id="orb-bbbb-0000",
        namespace="orb",
    )
    container = pod.spec.containers[0]
    assert container.image == "alpine:3.20"
    assert container.command == ["sh", "-c", "echo hi"]
    assert container.resources is not None
    assert container.resources.requests == {"cpu": "100m", "memory": "128Mi"}
    assert container.resources.limits == {"cpu": "500m", "memory": "256Mi"}


def test_build_pod_spec_applies_provider_config_defaults() -> None:
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
    pod = build_pod_spec(
        template,
        request,
        pod_name="orb-cccc-0000",
        machine_id="orb-cccc-0000",
        namespace="orb",
        config=config,
    )
    assert pod.spec.node_selector == {"role": "compute"}
    assert pod.spec.tolerations is not None
    assert len(pod.spec.tolerations) == 1
    assert pod.spec.tolerations[0].key == "dedicated"
    assert pod.spec.image_pull_secrets is not None
    assert pod.spec.image_pull_secrets[0].name == "my-pull-secret"


def test_build_pod_spec_requires_image() -> None:
    request = _build_request()
    template = Template(
        template_id="tpl-no-image",
        provider_type="k8s",
        provider_api="Pod",
        max_instances=1,
    )
    with pytest.raises(ValueError, match="container image"):
        build_pod_spec(
            template,
            request,
            pod_name="orb-dddd-0000",
            machine_id="orb-dddd-0000",
            namespace="default",
        )


def test_build_pod_spec_respects_custom_label_prefix() -> None:
    request = _build_request()
    template = _build_template()
    config = K8sProviderConfig(label_prefix="example.com", emit_legacy_labels=False)
    pod = build_pod_spec(
        template,
        request,
        pod_name="orb-eeee-0000",
        machine_id="orb-eeee-0000",
        namespace="default",
        config=config,
    )
    assert pod.metadata.labels is not None
    assert "example.com/managed" in pod.metadata.labels
    assert LEGACY_REQUEST_ID_LABEL not in pod.metadata.labels


# ---------------------------------------------------------------------------
# apply_pod_spec_override — restartPolicy invariant
# ---------------------------------------------------------------------------


def test_apply_pod_spec_override_preserves_restart_policy_never() -> None:
    """A benign override that does not touch restartPolicy must succeed."""
    from orb.providers.k8s.utilities.pod_spec import apply_pod_spec_override

    request = _build_request()
    template = _build_template()
    pod = build_pod_spec(
        template, request, pod_name="orb-x-0000", machine_id="orb-x-0000", namespace="default"
    )
    # Applying an innocuous override must not raise.
    patched = apply_pod_spec_override(pod, {"active_deadline_seconds": 3600})
    assert patched.spec.restart_policy == "Never"
    assert patched.spec.active_deadline_seconds == 3600


def test_apply_pod_spec_override_rejects_snake_case_restart_policy() -> None:
    """Supplying restart_policy != 'Never' in snake_case must raise K8sError."""
    from orb.providers.k8s.exceptions.k8s_errors import K8sError
    from orb.providers.k8s.utilities.pod_spec import apply_pod_spec_override

    request = _build_request()
    template = _build_template()
    pod = build_pod_spec(
        template, request, pod_name="orb-x-0001", machine_id="orb-x-0001", namespace="default"
    )
    with pytest.raises(K8sError, match="restart_policy"):
        apply_pod_spec_override(pod, {"restart_policy": "Always"})


def test_apply_pod_spec_override_rejects_camel_case_restart_policy() -> None:
    """Supplying restartPolicy != 'Never' in camelCase must raise K8sError."""
    from orb.providers.k8s.exceptions.k8s_errors import K8sError
    from orb.providers.k8s.utilities.pod_spec import apply_pod_spec_override

    request = _build_request()
    template = _build_template()
    pod = build_pod_spec(
        template, request, pod_name="orb-x-0002", machine_id="orb-x-0002", namespace="default"
    )
    with pytest.raises(K8sError, match="restartPolicy"):
        apply_pod_spec_override(pod, {"restartPolicy": "OnFailure"})


def test_apply_pod_spec_override_allows_explicit_never() -> None:
    """Passing restartPolicy=Never explicitly must not raise."""
    from orb.providers.k8s.utilities.pod_spec import apply_pod_spec_override

    request = _build_request()
    template = _build_template()
    pod = build_pod_spec(
        template, request, pod_name="orb-x-0003", machine_id="orb-x-0003", namespace="default"
    )
    patched = apply_pod_spec_override(pod, {"restartPolicy": "Never"})
    assert patched.spec.restart_policy == "Never"
