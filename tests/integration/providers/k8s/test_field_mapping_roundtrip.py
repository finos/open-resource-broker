"""Integration test for the HostFactory <-> internal field mapping roundtrip.

Covers the path a HostFactory template takes through ORB:

* HF camelCase JSON is mapped into the internal snake_case shape via
  the kubernetes-specific :class:`K8sFieldMapping` adapter
  (registered with :class:`FieldMappingRegistry` during provider
  bootstrap) plus the generic mappings the
  :class:`HostFactoryFieldMapper` always applies;
* internal defaults are applied via the adapter's
  :meth:`apply_defaults` (``namespace``, ``max_instances``,
  ``annotations``);
* mapping back to HF preserves the kubernetes-specific keys that the
  scheduler hands the operator on a ``getAvailableTemplates``
  round trip.

The test wires the registry by hand (no DI bootstrap), mirroring the
production registration in
:func:`orb.providers.k8s.registration._register_field_mapping`.

Shadow fields (``containerImage`` / ``labels`` / ``replicas``) are not
exercised here — those concepts are sourced from the generic ``imageId``,
``tags`` and ``maxNumber`` HF fields and the per-request
``requested_count`` respectively.
"""

from __future__ import annotations

from typing import Any

import pytest

from orb.infrastructure.scheduler.hostfactory.field_mapper import HostFactoryFieldMapper
from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import (
    FieldMappingRegistry,
)
from orb.infrastructure.scheduler.hostfactory.field_mappings import HostFactoryFieldMappings
from orb.providers.k8s.scheduler.hostfactory_field_mapping import K8sFieldMapping


def _hf_field_mappings() -> dict[str, str]:
    """Compose the full HF->internal mapping table that the scheduler uses.

    Mirrors the integration the registration layer wires up at runtime:
    the generic table plus the kubernetes-specific adapter table merged
    on top (adapter wins on conflicting keys, matching the AWS pattern).
    """
    base = HostFactoryFieldMappings.MAPPINGS["generic"].copy()
    base.update(K8sFieldMapping().get_mappings())
    return base


def _apply_full_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    """HF camelCase -> internal snake_case using the full kubernetes mapping table."""
    mapping = _hf_field_mappings()
    out: dict[str, Any] = {}
    for hf_key, internal_key in mapping.items():
        if hf_key in payload:
            out[internal_key] = payload[hf_key]
    return out


def _reverse_full_mapping(internal: dict[str, Any]) -> dict[str, Any]:
    """Internal snake_case -> HF camelCase using the full kubernetes mapping table."""
    mapping = _hf_field_mappings()
    reverse = {v: k for k, v in mapping.items()}
    out: dict[str, Any] = {}
    for internal_key, hf_key in reverse.items():
        if internal_key in internal:
            out[hf_key] = internal[internal_key]
    return out


@pytest.fixture(autouse=True)
def _register_k8s_adapter() -> None:
    """Register the kubernetes field-mapping adapter for the duration of the test.

    The registry survives between tests (it is a class-level singleton)
    so we clear it on teardown to avoid leaking state into other suites.
    """
    FieldMappingRegistry.register("k8s", K8sFieldMapping())
    yield
    FieldMappingRegistry.clear()


def _hf_payload(*, replicas: int = 3) -> dict[str, object]:
    """A realistic HF JSON payload for a kubernetes Deployment template.

    Uses the generic surfaces (``imageId``, ``tags``, ``maxNumber``) for
    the concepts that used to live in shadow k8s fields.
    """
    return {
        "templateId": "my-k8s-template",
        "maxNumber": replicas,
        "providerName": "kubernetes_orb-it",
        "providerApi": "Deployment",
        "providerType": "k8s",
        # Container image arrives via the generic ``imageId`` surface.
        "imageId": "ghcr.io/example/worker:1.2.3",
        "namespace": "orb-it",
        "resourceRequests": {"cpu": "500m", "memory": "256Mi"},
        "resourceLimits": {"cpu": "2", "memory": "1Gi"},
        "runtimeClass": "gvisor",
        "nodeSelector": {"role": "compute"},
        "tolerations": [{"key": "dedicated", "operator": "Equal", "value": "ml"}],
        "serviceAccount": "orb-worker",
        # Operator-supplied labels arrive via the generic ``instanceTags``
        # HF field which maps to ``tags`` on the domain ``Template``.
        "instanceTags": {"team": "ml"},
        "annotations": {"orb.io/note": "submitted-via-hf"},
        "env": {"WORKER_MODE": "batch"},
        "volumeMounts": [{"name": "data", "mountPath": "/data"}],
        "volumes": [{"name": "data", "emptyDir": {}}],
        "imagePullSecret": "registry-creds",
    }


def test_hf_to_internal_field_mapping_translates_camel_case() -> None:
    """HF camelCase fields land on the snake_case internal shape with defaults."""
    # Sanity check: HF mapper for the kubernetes provider type loads
    # the generic mappings even when no provider-specific table is
    # registered in the legacy ``HostFactoryFieldMappings`` dict.
    mapper = HostFactoryFieldMapper(provider_type="k8s")
    generic_only = mapper.map_input_fields({"templateId": "x", "maxNumber": 3})
    assert generic_only["template_id"] == "x"
    assert generic_only["max_instances"] == 3

    # Full mapping (generic + kubernetes adapter) translates every
    # kubernetes-specific HF key into the snake_case internal key.
    payload = _hf_payload(replicas=4)
    mapped = _apply_full_mapping(payload)

    assert mapped["template_id"] == "my-k8s-template"
    assert mapped["max_instances"] == 4
    assert mapped["provider_api"] == "Deployment"
    assert mapped["provider_type"] == "k8s"
    assert mapped["provider_name"] == "kubernetes_orb-it"

    # Image and tags come from the generic surfaces.
    assert mapped["image_id"] == "ghcr.io/example/worker:1.2.3"
    # ``instanceTags`` is the HF surface for the generic ``tags`` field.
    assert mapped["tags"] == {"team": "ml"}
    assert mapped["namespace"] == "orb-it"
    assert mapped["resource_requests"] == {"cpu": "500m", "memory": "256Mi"}
    assert mapped["resource_limits"] == {"cpu": "2", "memory": "1Gi"}
    assert mapped["runtime_class"] == "gvisor"
    assert mapped["node_selector"] == {"role": "compute"}
    assert mapped["tolerations"] == [{"key": "dedicated", "operator": "Equal", "value": "ml"}]
    assert mapped["service_account"] == "orb-worker"
    assert mapped["annotations"] == {"orb.io/note": "submitted-via-hf"}
    assert mapped["env"] == {"WORKER_MODE": "batch"}
    assert mapped["volume_mounts"] == [{"name": "data", "mountPath": "/data"}]
    assert mapped["volumes"] == [{"name": "data", "emptyDir": {}}]
    assert mapped["image_pull_secret"] == "registry-creds"

    # Shadow fields are intentionally absent on the internal side.
    assert "container_image" not in mapped
    assert "labels" not in mapped
    assert "replicas" not in mapped


def test_internal_to_hf_field_mapping_preserves_kubernetes_keys() -> None:
    """The reverse transformation surfaces the kubernetes-specific keys back to HF."""
    internal = {
        "template_id": "k8s-out",
        "max_instances": 2,
        "provider_api": "Pod",
        "provider_type": "k8s",
        "image_id": "busybox:latest",
        "tags": {"team": "ml"},
        "namespace": "orb-it",
        "resource_requests": {"cpu": "100m"},
        "resource_limits": {"cpu": "200m"},
        "runtime_class": "gvisor",
        "node_selector": {"role": "compute"},
        "service_account": "orb-worker",
        "annotations": {"orb.io/note": "round-trip"},
        "env": {"BACKEND": "queue"},
    }
    out = _reverse_full_mapping(internal)

    assert out["templateId"] == "k8s-out"
    assert out["maxNumber"] == 2
    assert out["providerApi"] == "Pod"
    assert out["providerType"] == "k8s"
    assert out["imageId"] == "busybox:latest"
    # ``tags`` reverses to ``instanceTags`` per the generic mapping.
    assert out["instanceTags"] == {"team": "ml"}
    assert out["namespace"] == "orb-it"
    assert out["resourceRequests"] == {"cpu": "100m"}
    assert out["resourceLimits"] == {"cpu": "200m"}
    assert out["runtimeClass"] == "gvisor"
    assert out["nodeSelector"] == {"role": "compute"}
    assert out["serviceAccount"] == "orb-worker"
    assert out["annotations"] == {"orb.io/note": "round-trip"}
    # env / environment both reverse-map to one canonical k8s-specific key.
    assert out.get("environment") == {"BACKEND": "queue"} or out.get("env") == (
        {"BACKEND": "queue"}
    )


def test_field_mapping_defaults_applied_in_isolation() -> None:
    """``apply_defaults`` populates kubernetes-sensible defaults for absent fields."""
    adapter = K8sFieldMapping()
    out = adapter.apply_defaults({})
    assert out["max_instances"] == 1
    assert out["annotations"] == {}
    # ``namespace`` is NOT defaulted here — the precedence chain at
    # ``K8sBaseHandler.resolve_namespace`` resolves
    # HF/template namespace -> provider-config namespace -> kube-API
    # default ``"default"``.  Hard-coding it here would short-circuit
    # the provider-config fallback.
    assert "namespace" not in out
    # Replicas / labels / env are intentionally NOT defaulted — those
    # concepts live on the generic ``requested_count`` / ``tags`` / typed
    # ``env`` surfaces instead.
    assert "replicas" not in out
    assert "labels" not in out
    assert "environment_variables" not in out

    # Operator-supplied values win over defaults.
    out = adapter.apply_defaults(
        {"namespace": "ns-a", "max_instances": 7, "annotations": {"k": "v"}}
    )
    assert out["namespace"] == "ns-a"
    assert out["max_instances"] == 7
    assert out["annotations"] == {"k": "v"}


def test_field_mapping_full_roundtrip_in_to_out() -> None:
    """HF payload in -> internal -> HF payload out preserves the user-visible keys."""
    payload = _hf_payload(replicas=2)

    internal = _apply_full_mapping(payload)
    K8sFieldMapping().apply_defaults(internal)
    out = _reverse_full_mapping(internal)

    assert out["templateId"] == payload["templateId"]
    assert out["maxNumber"] == payload["maxNumber"]
    assert out["providerApi"] == payload["providerApi"]
    assert out["imageId"] == payload["imageId"]
    assert out["instanceTags"] == payload["instanceTags"]
    assert out["namespace"] == payload["namespace"]
    assert out["resourceRequests"] == payload["resourceRequests"]
    assert out["resourceLimits"] == payload["resourceLimits"]
    assert out["runtimeClass"] == payload["runtimeClass"]
    assert out["nodeSelector"] == payload["nodeSelector"]
    assert out["serviceAccount"] == payload["serviceAccount"]
    assert out["annotations"] == payload["annotations"]


def test_registry_exposes_kubernetes_adapter() -> None:
    """The kubernetes adapter is reachable through ``FieldMappingRegistry.get``."""
    adapter = FieldMappingRegistry.get("k8s")
    assert adapter is not None
    # Kubernetes does not derive cpu/ram from machine-type strings.
    assert adapter.derive_attributes("custom-1") is None
