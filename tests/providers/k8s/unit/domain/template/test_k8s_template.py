"""Unit tests for :class:`K8sTemplate` and its supporting value objects.

Covers:

* the generic ``Template.machine_image`` is the single source of truth for
  the container image; shadow fields are gone.
* :attr:`K8sTemplate.service_account` falls back to
  :attr:`Template.machine_role` via the ``after`` model-validator.
* :meth:`K8sTemplate.resolve_pod_labels` projects ``Template.tags`` into
  string-keyed string-valued k8s labels, dropping ``None`` entries.
* :meth:`upcast_to_k8s_template` is a clean round-trip from a generic
  ``Template`` via ``model_dump`` / ``model_validate``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.domain.template.k8s_template_aggregate import (
    K8sEnvVar,
    K8sResourceQuantities,
    K8sTemplate,
    K8sToleration,
    K8sVolume,
    upcast_to_k8s_template,
)

# ---------------------------------------------------------------------------
# Construction + provider_type defaulting
# ---------------------------------------------------------------------------


def test_k8s_template_sets_provider_type_to_k8s() -> None:
    t = K8sTemplate(template_id="tpl", image_id="busybox:latest")
    assert t.provider_type == "k8s"


def test_k8s_template_requires_template_id() -> None:
    with pytest.raises(ValidationError):
        K8sTemplate()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# image_id is the single source of truth — no more shadow ``container_image``
# ---------------------------------------------------------------------------


def test_image_id_is_required_for_pod_spec_resolution() -> None:
    t = K8sTemplate(template_id="tpl")
    with pytest.raises(ValueError, match="image"):
        t.resolve_container_image()


def test_resolve_container_image_returns_image_id() -> None:
    t = K8sTemplate(template_id="tpl", image_id="ghcr.io/example/worker:1")
    assert t.resolve_container_image() == "ghcr.io/example/worker:1"


# ---------------------------------------------------------------------------
# F4 — image_id validation rejects invalid Docker image names
# ---------------------------------------------------------------------------


def test_k8s_template_rejects_image_with_spaces() -> None:
    """K8sTemplate must reject image names containing whitespace at construction."""
    with pytest.raises(ValidationError, match="Invalid container image name"):
        K8sTemplate(
            template_id="tpl",
            image_id="INVALID IMAGE NAME WITH SPACES!!!",
        )


def test_k8s_template_accepts_valid_image_names() -> None:
    """K8sTemplate must accept common Docker image name patterns."""
    for image in [
        "busybox:latest",
        "gcr.io/google-containers/pause:3.1",
        "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-image:tag",
    ]:
        t = K8sTemplate(template_id="tpl", image_id=image)
        assert t.machine_image == image


def test_k8s_template_accepts_none_image_id() -> None:
    """None image_id is allowed."""
    t = K8sTemplate(template_id="tpl", image_id=None)
    assert t.machine_image is None


def test_extension_config_does_not_define_container_image() -> None:
    """The shadow field is gone — ``container_image`` lives on no extension surface."""
    from orb.providers.k8s.configuration.template_extension import (
        K8sTemplateExtensionConfig,
    )
    from orb.providers.k8s.domain.template.k8s_template_dto_config import (
        K8sTemplateDTOConfig,
    )

    assert "container_image" not in K8sTemplateExtensionConfig.model_fields
    assert "container_image" not in K8sTemplateDTOConfig.model_fields


# ---------------------------------------------------------------------------
# service_account fallback to machine_role
# ---------------------------------------------------------------------------


def test_service_account_falls_back_to_machine_role() -> None:
    t = K8sTemplate(
        template_id="tpl",
        image_id="busybox:latest",
        machine_role="my-svc-acct",
    )
    assert t.service_account == "my-svc-acct"


def test_service_account_falls_back_to_machine_role_via_deprecated_instance_profile() -> None:
    """Old instance_profile kwarg still works via the DeprecationWarning path."""
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        t = K8sTemplate(
            template_id="tpl",
            image_id="busybox:latest",
            instance_profile="my-svc-acct",
        )
        assert any(issubclass(warning.category, DeprecationWarning) for warning in w)
    assert t.service_account == "my-svc-acct"


def test_explicit_service_account_wins_over_machine_role() -> None:
    t = K8sTemplate(
        template_id="tpl",
        image_id="busybox:latest",
        machine_role="machine-role-name",
        service_account="explicit-sa",
    )
    assert t.service_account == "explicit-sa"


def test_no_machine_role_leaves_service_account_unset() -> None:
    t = K8sTemplate(template_id="tpl", image_id="busybox:latest")
    assert t.service_account is None


# ---------------------------------------------------------------------------
# tags -> pod labels projection
# ---------------------------------------------------------------------------


def test_resolve_pod_labels_coerces_values_to_strings() -> None:
    t = K8sTemplate(
        template_id="tpl",
        image_id="busybox:latest",
        tags={"team": "ml", "tier": 3, "active": True, "deprecated": None},
    )
    labels = t.resolve_pod_labels()
    assert labels == {"team": "ml", "tier": "3", "active": "True"}
    # ``None`` entries are dropped — k8s does not allow nulls in label values.
    assert "deprecated" not in labels


def test_resolve_pod_labels_empty_when_no_tags() -> None:
    t = K8sTemplate(template_id="tpl", image_id="busybox:latest")
    assert t.resolve_pod_labels() == {}


# ---------------------------------------------------------------------------
# Round-trip from generic Template
# ---------------------------------------------------------------------------


def test_upcast_from_generic_template_round_trips_cleanly() -> None:
    base = Template(
        template_id="tpl",
        image_id="busybox:latest",
        max_instances=4,
        tags={"team": "ml"},
        machine_role="svc-acct",
    )
    k8s = upcast_to_k8s_template(base)
    assert isinstance(k8s, K8sTemplate)
    assert k8s.template_id == "tpl"
    assert k8s.machine_image == "busybox:latest"
    assert k8s.max_machines == 4
    assert k8s.tags == {"team": "ml"}
    # Service-account fallback runs on the upcast model.
    assert k8s.service_account == "svc-acct"
    # k8s-specific fields default to None.
    assert k8s.namespace is None
    assert k8s.runtime_class is None


def test_upcast_is_idempotent_on_k8s_template() -> None:
    t = K8sTemplate(template_id="tpl", image_id="busybox:latest", namespace="orb")
    assert upcast_to_k8s_template(t) is t


def test_model_validate_round_trip_via_model_dump() -> None:
    """``K8sTemplate.model_validate(template.model_dump())`` is lossless."""
    original = K8sTemplate(
        template_id="tpl",
        image_id="busybox:latest",
        namespace="orb",
        max_instances=2,
        tolerations=[
            K8sToleration(key="dedicated", operator="Equal", value="orb", effect="NoSchedule")
        ],
        resource_requests=K8sResourceQuantities(cpu="500m", memory="1Gi"),
        env=[K8sEnvVar(name="FOO", value="bar")],
    )
    revived = K8sTemplate.model_validate(original.model_dump())
    assert revived.namespace == "orb"
    assert revived.machine_image == "busybox:latest"
    assert revived.tolerations is not None
    assert revived.tolerations[0].key == "dedicated"
    assert revived.resource_requests is not None
    assert revived.resource_requests.cpu == "500m"
    assert revived.env is not None
    assert revived.env[0].name == "FOO"


# ---------------------------------------------------------------------------
# provider_config (DTO round-trip) promotion
# ---------------------------------------------------------------------------


def test_provider_config_dict_promotes_namespace_when_unset() -> None:
    t = K8sTemplate(
        template_id="tpl",
        image_id="busybox:latest",
        provider_config={"namespace": "from-dto"},
    )
    assert t.namespace == "from-dto"


def test_direct_field_wins_over_provider_config_dict() -> None:
    t = K8sTemplate(
        template_id="tpl",
        image_id="busybox:latest",
        namespace="direct",
        provider_config={"namespace": "from-dto"},
    )
    assert t.namespace == "direct"


def test_provider_config_dict_promotes_resource_requests() -> None:
    t = K8sTemplate(
        template_id="tpl",
        image_id="busybox:latest",
        provider_config={"resource_requests": {"cpu": "250m"}},
    )
    assert t.resource_requests is not None
    assert t.resource_requests.cpu == "250m"


# ---------------------------------------------------------------------------
# Value-object coercion
# ---------------------------------------------------------------------------


def test_resource_quantities_drop_unset_keys() -> None:
    q = K8sResourceQuantities(cpu="500m")
    assert q.to_resource_map() == {"cpu": "500m"}


def test_resource_quantities_emit_gpu_resource() -> None:
    q = K8sResourceQuantities(gpu_type="nvidia.com/gpu", gpu_count=2)
    assert q.to_resource_map() == {"nvidia.com/gpu": "2"}


def test_env_var_rejects_value_and_value_from() -> None:
    with pytest.raises(ValidationError):
        K8sEnvVar(name="FOO", value="bar", value_from={"fieldRef": {"fieldPath": "x"}})


def test_volume_accepts_inline_source_shape() -> None:
    """Common ``{"name": "data", "emptyDir": {}}`` shape is accepted."""
    from orb.providers.k8s.domain.template.k8s_template_aggregate import _coerce_volumes

    out = _coerce_volumes([{"name": "data", "emptyDir": {}}])
    assert out is not None
    assert out[0].name == "data"
    assert out[0].source == {"emptyDir": {}}


# ---------------------------------------------------------------------------
# K8sTemplate.namespace validator
# ---------------------------------------------------------------------------


def test_namespace_rejects_blank_string() -> None:
    with pytest.raises(ValidationError):
        K8sTemplate(template_id="tpl", image_id="busybox:latest", namespace=" ")


# ---------------------------------------------------------------------------
# pod_spec_override is a flat dict and not validated structurally
# ---------------------------------------------------------------------------


def test_pod_spec_override_accepts_arbitrary_dict() -> None:
    t = K8sTemplate(
        template_id="tpl",
        image_id="busybox:latest",
        pod_spec_override={"hostNetwork": True, "dnsPolicy": "ClusterFirst"},
    )
    assert t.pod_spec_override == {"hostNetwork": True, "dnsPolicy": "ClusterFirst"}


# ---------------------------------------------------------------------------
# Unused imports
# ---------------------------------------------------------------------------


def test_value_object_imports_are_exported() -> None:
    """``K8sVolume`` is exported alongside the other value objects."""
    assert K8sVolume.__module__.endswith("k8s_template_aggregate")
