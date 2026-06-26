"""Unit tests for ``K8sFieldMapping``.

Mirrors the test surface of the AWS field-mapping in spirit: covers the
provider-specific HF -> internal mapping table, defaults application, and
the ``derive_attributes`` fallback behaviour.
"""

from __future__ import annotations

import pytest

from orb.infrastructure.scheduler.hostfactory.field_mapping_port import FieldMappingPort
from orb.providers.k8s.scheduler.hostfactory_field_mapping import K8sFieldMapping


@pytest.fixture()
def mapping() -> K8sFieldMapping:
    return K8sFieldMapping()


def test_satisfies_field_mapping_port(mapping: K8sFieldMapping) -> None:
    """The adapter satisfies the runtime-checkable ``FieldMappingPort`` protocol."""
    assert isinstance(mapping, FieldMappingPort)


def test_get_mappings_contains_core_entries(mapping: K8sFieldMapping) -> None:
    """Every kubernetes-specific HF field is mapped to a snake_case name."""
    mappings = mapping.get_mappings()
    assert mappings["namespace"] == "namespace"
    assert mappings["resourceRequests"] == "resource_requests"
    assert mappings["resourceLimits"] == "resource_limits"
    assert mappings["runtimeClass"] == "runtime_class"
    assert mappings["nodeSelector"] == "node_selector"
    assert mappings["tolerations"] == "tolerations"
    assert mappings["serviceAccount"] == "service_account"
    assert mappings["completions"] == "completions"
    assert mappings["parallelism"] == "parallelism"
    assert mappings["annotations"] == "annotations"
    assert mappings["volumeMounts"] == "volume_mounts"
    assert mappings["volumes"] == "volumes"
    # env / environment both collapse to the typed ``env`` field.
    assert mappings["env"] == "env"
    assert mappings["environment"] == "env"
    assert mappings["imagePullSecret"] == "image_pull_secret"
    assert mappings["podSpecOverride"] == "pod_spec_override"


def test_shadow_fields_not_in_mappings(mapping: K8sFieldMapping) -> None:
    """Generic shadow fields are absent — operators use the generic surface."""
    mappings = mapping.get_mappings()
    # Container image goes through the generic ``imageId`` -> ``image_id`` mapping.
    assert "containerImage" not in mappings
    # Labels merge into ``Template.tags`` at spec-build time.
    assert "labels" not in mappings
    # Replica count comes from ``request.requested_count`` (HF ``maxNumber``).
    assert "replicas" not in mappings


def test_get_mappings_returns_copy(mapping: K8sFieldMapping) -> None:
    """Mutating the returned dict does not corrupt the adapter's internal state."""
    first = mapping.get_mappings()
    first["namespace"] = "tampered"
    second = mapping.get_mappings()
    assert second["namespace"] == "namespace"


def test_apply_defaults_fills_unset_keys(mapping: K8sFieldMapping) -> None:
    """Unset fields are filled with kubernetes-sensible defaults."""
    out = mapping.apply_defaults({})
    assert out["namespace"] == "default"
    assert out["max_instances"] == 1
    assert out["annotations"] == {}
    # Replicas / labels / env are intentionally NOT defaulted — they are
    # derived from generic surfaces (``requested_count`` / ``tags``)
    # or absent until the operator sets them.
    assert "replicas" not in out
    assert "labels" not in out
    assert "environment_variables" not in out


def test_apply_defaults_preserves_explicit_values(mapping: K8sFieldMapping) -> None:
    """Operator-supplied values win against the defaults."""
    out = mapping.apply_defaults(
        {
            "namespace": "orb-prod",
            "max_instances": 5,
            "annotations": {"orb.io/note": "hello"},
        }
    )
    assert out["namespace"] == "orb-prod"
    assert out["max_instances"] == 5
    assert out["annotations"] == {"orb.io/note": "hello"}


def test_derive_attributes_returns_none(mapping: K8sFieldMapping) -> None:
    """The kubernetes provider does not infer cpu/ram from a machine-type string."""
    assert mapping.derive_attributes(None) is None
    assert mapping.derive_attributes("") is None
    assert mapping.derive_attributes("custom-1") is None
