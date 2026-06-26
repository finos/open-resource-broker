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
    assert mappings["containerImage"] == "container_image"
    assert mappings["namespace"] == "namespace"
    assert mappings["resourceRequests"] == "resource_requests"
    assert mappings["resourceLimits"] == "resource_limits"
    assert mappings["runtimeClass"] == "runtime_class"
    assert mappings["nodeSelector"] == "node_selector"
    assert mappings["tolerations"] == "tolerations"
    assert mappings["serviceAccount"] == "service_account"
    assert mappings["completions"] == "completions"
    assert mappings["parallelism"] == "parallelism"
    assert mappings["replicas"] == "replicas"
    assert mappings["labels"] == "labels"
    assert mappings["annotations"] == "annotations"
    assert mappings["volumeMounts"] == "volume_mounts"
    assert mappings["volumes"] == "volumes"
    # env / environment both collapse to the snake_case internal name
    assert mappings["env"] == "environment_variables"
    assert mappings["environment"] == "environment_variables"


def test_get_mappings_returns_copy(mapping: K8sFieldMapping) -> None:
    """Mutating the returned dict does not corrupt the adapter's internal state."""
    first = mapping.get_mappings()
    first["containerImage"] = "tampered"
    second = mapping.get_mappings()
    assert second["containerImage"] == "container_image"


def test_apply_defaults_fills_unset_keys(mapping: K8sFieldMapping) -> None:
    """Unset fields are filled with kubernetes-sensible defaults."""
    out = mapping.apply_defaults({})
    assert out["namespace"] == "default"
    assert out["max_instances"] == 1
    assert out["replicas"] == 1
    assert out["labels"] == {}
    assert out["annotations"] == {}
    assert out["environment_variables"] == {}


def test_apply_defaults_preserves_explicit_values(mapping: K8sFieldMapping) -> None:
    """Operator-supplied values win against the defaults."""
    out = mapping.apply_defaults(
        {
            "namespace": "orb-prod",
            "max_instances": 5,
            "replicas": 3,
            "labels": {"team": "ml"},
            "annotations": {"orb.io/note": "hello"},
            "environment_variables": {"FOO": "bar"},
        }
    )
    assert out["namespace"] == "orb-prod"
    assert out["max_instances"] == 5
    assert out["replicas"] == 3
    assert out["labels"] == {"team": "ml"}
    assert out["annotations"] == {"orb.io/note": "hello"}
    assert out["environment_variables"] == {"FOO": "bar"}


def test_apply_defaults_replicas_follow_max_instances(mapping: K8sFieldMapping) -> None:
    """When ``replicas`` is unset but ``max_instances`` is explicit, replicas track it."""
    out = mapping.apply_defaults({"max_instances": 7})
    assert out["replicas"] == 7


def test_derive_attributes_returns_none(mapping: K8sFieldMapping) -> None:
    """The kubernetes provider does not infer cpu/ram from a machine-type string."""
    assert mapping.derive_attributes(None) is None
    assert mapping.derive_attributes("") is None
    assert mapping.derive_attributes("custom-1") is None
