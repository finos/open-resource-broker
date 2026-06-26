"""Integration test for the HostFactory <-> internal field mapping roundtrip.

Covers the path a HostFactory template takes through ORB:

* HF camelCase JSON is mapped into the internal snake_case shape via
  the kubernetes-specific :class:`KubernetesFieldMapping` adapter
  (registered with :class:`FieldMappingRegistry` during provider
  bootstrap) plus the generic mappings the
  :class:`HostFactoryFieldMapper` always applies;
* internal defaults are applied via the adapter's
  :meth:`apply_defaults` (``namespace``, ``replicas``,
  ``labels`` / ``annotations``, ``environment_variables``);
* mapping back to HF preserves the kubernetes-specific keys that the
  scheduler hands the operator on a ``getAvailableTemplates``
  round trip.

The test wires the registry by hand (no DI bootstrap), mirroring the
production registration in
:func:`orb.providers.kubernetes.registration._register_field_mapping`.
"""

from __future__ import annotations

from typing import Any

import pytest

from orb.infrastructure.scheduler.hostfactory.field_mapper import HostFactoryFieldMapper
from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import (
    FieldMappingRegistry,
)
from orb.infrastructure.scheduler.hostfactory.field_mappings import HostFactoryFieldMappings
from orb.providers.kubernetes.scheduler.hostfactory_field_mapping import KubernetesFieldMapping


def _hf_field_mappings() -> dict[str, str]:
    """Compose the full HF->internal mapping table that the scheduler uses.

    Mirrors the integration the registration layer wires up at runtime:
    the generic table plus the kubernetes-specific adapter table merged
    on top (adapter wins on conflicting keys, matching the AWS pattern).
    """
    base = HostFactoryFieldMappings.MAPPINGS["generic"].copy()
    base.update(KubernetesFieldMapping().get_mappings())
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
def _register_kubernetes_adapter() -> None:
    """Register the kubernetes field-mapping adapter for the duration of the test.

    The registry survives between tests (it is a class-level singleton)
    so we clear it on teardown to avoid leaking state into other suites.
    """
    FieldMappingRegistry.register("kubernetes", KubernetesFieldMapping())
    yield
    FieldMappingRegistry.clear()


def _hf_payload(*, replicas: int = 3) -> dict[str, object]:
    """A realistic HF JSON payload for a kubernetes Deployment template."""
    return {
        "templateId": "my-k8s-template",
        "maxNumber": replicas,
        "providerName": "kubernetes_orb-it",
        "providerApi": "KubernetesDeployment",
        "providerType": "kubernetes",
        "containerImage": "ghcr.io/example/worker:1.2.3",
        "namespace": "orb-it",
        "resourceRequests": {"cpu": "500m", "memory": "256Mi"},
        "resourceLimits": {"cpu": "2", "memory": "1Gi"},
        "runtimeClass": "gvisor",
        "nodeSelector": {"role": "compute"},
        "tolerations": [{"key": "dedicated", "operator": "Equal", "value": "ml"}],
        "serviceAccount": "orb-worker",
        "replicas": replicas,
        "labels": {"team": "ml"},
        "annotations": {"orb.io/note": "submitted-via-hf"},
        "env": {"WORKER_MODE": "batch"},
        "volumeMounts": [{"name": "data", "mountPath": "/data"}],
        "volumes": [{"name": "data", "emptyDir": {}}],
    }


def test_hf_to_internal_field_mapping_translates_camel_case() -> None:
    """HF camelCase fields land on the snake_case internal shape with defaults."""
    # Sanity check: HF mapper for the kubernetes provider type loads
    # the generic mappings even when no provider-specific table is
    # registered in the legacy ``HostFactoryFieldMappings`` dict.
    mapper = HostFactoryFieldMapper(provider_type="kubernetes")
    generic_only = mapper.map_input_fields({"templateId": "x", "maxNumber": 3})
    assert generic_only["template_id"] == "x"
    assert generic_only["max_instances"] == 3

    # Full mapping (generic + kubernetes adapter) translates every
    # kubernetes-specific HF key into the snake_case internal key.
    payload = _hf_payload(replicas=4)
    mapped = _apply_full_mapping(payload)

    assert mapped["template_id"] == "my-k8s-template"
    assert mapped["max_instances"] == 4
    assert mapped["provider_api"] == "KubernetesDeployment"
    assert mapped["provider_type"] == "kubernetes"
    assert mapped["provider_name"] == "kubernetes_orb-it"

    assert mapped["container_image"] == "ghcr.io/example/worker:1.2.3"
    assert mapped["namespace"] == "orb-it"
    assert mapped["resource_requests"] == {"cpu": "500m", "memory": "256Mi"}
    assert mapped["resource_limits"] == {"cpu": "2", "memory": "1Gi"}
    assert mapped["runtime_class"] == "gvisor"
    assert mapped["node_selector"] == {"role": "compute"}
    assert mapped["tolerations"] == [{"key": "dedicated", "operator": "Equal", "value": "ml"}]
    assert mapped["service_account"] == "orb-worker"
    assert mapped["replicas"] == 4
    assert mapped["labels"] == {"team": "ml"}
    assert mapped["annotations"] == {"orb.io/note": "submitted-via-hf"}
    assert mapped["environment_variables"] == {"WORKER_MODE": "batch"}
    assert mapped["volume_mounts"] == [{"name": "data", "mountPath": "/data"}]
    assert mapped["volumes"] == [{"name": "data", "emptyDir": {}}]


def test_internal_to_hf_field_mapping_preserves_kubernetes_keys() -> None:
    """The reverse transformation surfaces the kubernetes-specific keys back to HF."""
    internal = {
        "template_id": "k8s-out",
        "max_instances": 2,
        "provider_api": "KubernetesPod",
        "provider_type": "kubernetes",
        "container_image": "busybox:latest",
        "namespace": "orb-it",
        "resource_requests": {"cpu": "100m"},
        "resource_limits": {"cpu": "200m"},
        "runtime_class": "gvisor",
        "node_selector": {"role": "compute"},
        "service_account": "orb-worker",
        "labels": {"team": "ml"},
        "annotations": {"orb.io/note": "round-trip"},
        "environment_variables": {"BACKEND": "queue"},
    }
    out = _reverse_full_mapping(internal)

    assert out["templateId"] == "k8s-out"
    assert out["maxNumber"] == 2
    assert out["providerApi"] == "KubernetesPod"
    assert out["providerType"] == "kubernetes"
    assert out["containerImage"] == "busybox:latest"
    assert out["namespace"] == "orb-it"
    assert out["resourceRequests"] == {"cpu": "100m"}
    assert out["resourceLimits"] == {"cpu": "200m"}
    assert out["runtimeClass"] == "gvisor"
    assert out["nodeSelector"] == {"role": "compute"}
    assert out["serviceAccount"] == "orb-worker"
    assert out["labels"] == {"team": "ml"}
    assert out["annotations"] == {"orb.io/note": "round-trip"}
    # env / environment both map to environment_variables in the
    # forward direction.  The reverse direction picks one canonical
    # key; we accept either (long form is preferred in practice).
    assert out.get("environment") == {"BACKEND": "queue"} or out.get("env") == (
        {"BACKEND": "queue"}
    )


def test_field_mapping_defaults_applied_in_isolation() -> None:
    """``apply_defaults`` populates kubernetes-sensible defaults for absent fields."""
    adapter = KubernetesFieldMapping()
    out = adapter.apply_defaults({})
    assert out["namespace"] == "default"
    assert out["max_instances"] == 1
    assert out["replicas"] == 1
    assert out["labels"] == {}
    assert out["annotations"] == {}
    assert out["environment_variables"] == {}

    # Operator-supplied values win over defaults.
    out = adapter.apply_defaults({"namespace": "ns-a", "max_instances": 7, "labels": {"k": "v"}})
    assert out["namespace"] == "ns-a"
    assert out["max_instances"] == 7
    # ``replicas`` defaults to ``max_instances`` when not set.
    assert out["replicas"] == 7
    assert out["labels"] == {"k": "v"}


def test_field_mapping_full_roundtrip_in_to_out() -> None:
    """HF payload in -> internal -> HF payload out preserves the user-visible keys."""
    payload = _hf_payload(replicas=2)

    internal = _apply_full_mapping(payload)
    KubernetesFieldMapping().apply_defaults(internal)
    out = _reverse_full_mapping(internal)

    assert out["templateId"] == payload["templateId"]
    assert out["maxNumber"] == payload["maxNumber"]
    assert out["providerApi"] == payload["providerApi"]
    assert out["containerImage"] == payload["containerImage"]
    assert out["namespace"] == payload["namespace"]
    assert out["resourceRequests"] == payload["resourceRequests"]
    assert out["resourceLimits"] == payload["resourceLimits"]
    assert out["runtimeClass"] == payload["runtimeClass"]
    assert out["nodeSelector"] == payload["nodeSelector"]
    assert out["serviceAccount"] == payload["serviceAccount"]
    assert out["labels"] == payload["labels"]
    assert out["annotations"] == payload["annotations"]


def test_registry_exposes_kubernetes_adapter() -> None:
    """The kubernetes adapter is reachable through ``FieldMappingRegistry.get``."""
    adapter = FieldMappingRegistry.get("kubernetes")
    assert adapter is not None
    # Kubernetes does not derive cpu/ram from machine-type strings.
    assert adapter.derive_attributes("custom-1") is None
