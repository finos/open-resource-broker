"""Unit tests for :class:`K8sTemplateAdapter` conversion and validation methods.

Group T1 backfill: direct tests for validate_template, extend_template_fields,
validate_field_values, get_supported_fields, get_supported_provider_apis, and
get_adapter_info.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.infrastructure.adapters.template_adapter import K8sTemplateAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> K8sTemplateAdapter:
    """Return a K8sTemplateAdapter with all dependencies mocked."""
    return K8sTemplateAdapter(
        template_config_manager=MagicMock(),
        kubernetes_client=MagicMock(),
        logger=MagicMock(),
    )


def _make_k8s_template(**kwargs: Any) -> Any:
    from orb.providers.k8s.domain.template.k8s_template import K8sTemplate  # noqa: PLC0415

    defaults: dict[str, Any] = {
        "template_id": "tpl-unit",
        "provider_api": "Pod",
        "image_id": "busybox:latest",
        "max_instances": 4,
    }
    defaults.update(kwargs)
    return K8sTemplate(**defaults)


# ---------------------------------------------------------------------------
# get_supported_fields
# ---------------------------------------------------------------------------


def test_get_supported_fields_returns_expected_names() -> None:
    """get_supported_fields must include the canonical k8s template fields."""
    adapter = _make_adapter()
    fields = adapter.get_supported_fields()

    assert "namespace" in fields
    assert "resource_requests" in fields
    assert "resource_limits" in fields
    assert "runtime_class" in fields
    assert "node_selector" in fields
    assert "tolerations" in fields
    assert "service_account" in fields
    assert "env" in fields


def test_get_supported_fields_returns_copy() -> None:
    """Mutating the returned list must not affect subsequent calls."""
    adapter = _make_adapter()
    fields1 = adapter.get_supported_fields()
    fields1.clear()
    fields2 = adapter.get_supported_fields()
    assert len(fields2) > 0


# ---------------------------------------------------------------------------
# get_supported_provider_apis
# ---------------------------------------------------------------------------


def test_get_supported_provider_apis_covers_all_workloads() -> None:
    """get_supported_provider_apis must return Pod, Deployment, StatefulSet, Job."""
    adapter = _make_adapter()
    apis = adapter.get_supported_provider_apis()
    assert set(apis) == {"Pod", "Deployment", "StatefulSet", "Job"}


# ---------------------------------------------------------------------------
# get_provider_api
# ---------------------------------------------------------------------------


def test_get_provider_api_returns_pod() -> None:
    assert _make_adapter().get_provider_api() == "Pod"


# ---------------------------------------------------------------------------
# extend_template_fields
# ---------------------------------------------------------------------------


def test_extend_template_fields_defaults_provider_api_to_pod() -> None:
    """extend_template_fields must set provider_api='Pod' when it is absent."""
    from orb.domain.template.template_aggregate import Template  # noqa: PLC0415

    template = Template(
        template_id="tpl-x",
        provider_type="k8s",
        image_id="busybox:latest",
        max_instances=1,
    )
    adapter = _make_adapter()
    result = adapter.extend_template_fields(template)

    assert result.provider_api == "Pod"


def test_extend_template_fields_preserves_existing_provider_api() -> None:
    """extend_template_fields must not override a provider_api already set."""
    tpl = _make_k8s_template(provider_api="Deployment")
    adapter = _make_adapter()
    result = adapter.extend_template_fields(tpl)
    assert result.provider_api == "Deployment"


# ---------------------------------------------------------------------------
# validate_field_values
# ---------------------------------------------------------------------------


def test_validate_field_values_accepts_valid_template() -> None:
    """A well-formed k8s template with valid resource quantities produces no errors."""
    from orb.providers.k8s.domain.template.k8s_template import (  # noqa: PLC0415
        K8sResourceQuantities,
    )

    tpl = _make_k8s_template(
        namespace="orb-unit",
        resource_requests=K8sResourceQuantities(cpu="100m", memory="64Mi"),
        resource_limits=K8sResourceQuantities(cpu="500m", memory="256Mi"),
    )
    adapter = _make_adapter()
    errors = adapter.validate_field_values(tpl)
    assert errors == {}


def test_validate_field_values_requires_image_id() -> None:
    """A template without image_id must produce an error on the image_id field."""
    from orb.providers.k8s.domain.template.k8s_template import K8sTemplate  # noqa: PLC0415

    tpl = K8sTemplate(
        template_id="tpl-no-image",
        provider_api="Pod",
        max_instances=1,
        # image_id deliberately absent
    )
    adapter = _make_adapter()
    errors = adapter.validate_field_values(tpl)
    assert "image_id" in errors


def test_validate_field_values_rejects_invalid_namespace() -> None:
    """A namespace that does not conform to DNS-1123 must produce a namespace error."""
    tpl = _make_k8s_template(namespace="INVALID_NAMESPACE")
    adapter = _make_adapter()
    errors = adapter.validate_field_values(tpl)
    assert "namespace" in errors


def test_validate_field_values_rejects_invalid_resource_quantity() -> None:
    """An invalid resource quantity string must produce a resource_requests error."""
    from orb.providers.k8s.domain.template.k8s_template import (  # noqa: PLC0415
        K8sResourceQuantities,
    )

    tpl = _make_k8s_template(
        resource_requests=K8sResourceQuantities(cpu="not-a-quantity"),
    )
    adapter = _make_adapter()
    errors = adapter.validate_field_values(tpl)
    assert "resource_requests" in errors


def test_validate_field_values_rejects_zero_completions() -> None:
    """completions <= 0 must produce an error from the adapter logic.

    K8sTemplate validates completions >= 1 at the model level too, so we
    feed a negative-integer value via a stub to reach the adapter's own
    check without triggering the model-level guard.
    """
    from unittest.mock import MagicMock  # noqa: PLC0415

    from orb.providers.k8s.domain.template.k8s_template import K8sTemplate  # noqa: PLC0415

    # Use a valid template as the base, then stub out completions to a
    # negative value by wrapping the attribute at the adapter-call level.
    tpl = _make_k8s_template(provider_api="Job")

    # Monkey-patch the completions attribute for this test only so the
    # adapter's validate_field_values path that checks int(value) <= 0
    # is exercised.  We do this by patching the resolved attribute on the
    # upcast result — the easiest approach without modifying src/.
    original_completions = tpl.completions
    object.__setattr__(tpl, "completions", -1)
    try:
        adapter = _make_adapter()
        errors = adapter.validate_field_values(tpl)
    finally:
        object.__setattr__(tpl, "completions", original_completions)

    assert "completions" in errors


# ---------------------------------------------------------------------------
# validate_template
# ---------------------------------------------------------------------------


def test_validate_template_accepts_well_formed_template() -> None:
    """A correctly configured template must return no validation errors."""
    tpl = _make_k8s_template(namespace="orb-unit")
    adapter = _make_adapter()
    errors = adapter.validate_template(tpl)
    assert errors == []


def test_validate_required_fields_rejects_empty_template_id() -> None:
    """_validate_required_fields must return an error when template_id is empty.

    The method is the first check in validate_template.  We call it directly
    to verify the guard is in place, avoiding the model-level validation that
    prevents constructing a Template with an empty ID.
    """
    from unittest.mock import MagicMock  # noqa: PLC0415

    tpl = MagicMock()
    tpl.template_id = ""
    tpl.provider_api = "Pod"

    adapter = _make_adapter()
    errors = adapter._validate_required_fields(tpl)  # noqa: SLF001
    assert any("template_id" in e for e in errors)


def test_validate_template_rejects_unknown_provider_api() -> None:
    """A template with an unknown provider_api must produce an error."""
    from orb.domain.template.template_aggregate import Template  # noqa: PLC0415

    tpl = Template(
        template_id="tpl-bad-api",
        provider_type="k8s",
        provider_api="CronJob",
        image_id="busybox:latest",
        max_instances=1,
    )
    adapter = _make_adapter()
    errors = adapter.validate_template(tpl)
    assert any("CronJob" in e for e in errors)


# ---------------------------------------------------------------------------
# resolve_template_references — is a pass-through for k8s
# ---------------------------------------------------------------------------


def test_resolve_template_references_returns_same_template() -> None:
    """resolve_template_references must return the template unchanged."""
    tpl = _make_k8s_template()
    adapter = _make_adapter()
    result = adapter.resolve_template_references(tpl)
    assert result is tpl


# ---------------------------------------------------------------------------
# get_adapter_info
# ---------------------------------------------------------------------------


def test_get_adapter_info_shape() -> None:
    """get_adapter_info must return the expected metadata dict."""
    adapter = _make_adapter()
    info = adapter.get_adapter_info()

    assert info["adapter_name"] == "K8sTemplateAdapter"
    assert info["provider_type"] == "k8s"
    assert isinstance(info["supported_apis"], list)
    assert isinstance(info["supported_fields"], list)
    assert "field_validation" in info.get("features", [])
