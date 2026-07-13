"""Tests that the generic template validator delegates to provider rules.

The CLI `orb templates validate` path reaches
TemplateConfigurationAdapter.validate_template_config.  It must run the active
provider's registered validator so provider-specific rules (e.g. the k8s
validator rejecting an unknown provider_api) apply — not just the generic
present/absent field checks.

Regression tests verify that:
- bad namespace (INVALID_UPPER) returns a non-empty error list (Fix 1)
- max_instances=0 returns a non-empty error list (Fix 1)
- absent provider_api is not an error for k8s (Fix 2)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from orb.infrastructure.adapters.template_configuration_adapter import (
    TemplateConfigurationAdapter,
)
from orb.providers.k8s.validation.template_validator import K8sTemplateValidator


def _adapter_with_k8s_validator() -> TemplateConfigurationAdapter:
    tm = MagicMock()
    tm._registry.create_validator = lambda pt: K8sTemplateValidator() if pt == "k8s" else None
    return TemplateConfigurationAdapter(template_manager=tm, logger=MagicMock())


def test_unknown_provider_api_rejected_via_provider_type() -> None:
    a = _adapter_with_k8s_validator()
    errors = a.validate_template_config(
        {
            "template_id": "t",
            "image_id": "nginx",
            "provider_api": "BogusWorkload",
            "provider_type": "k8s",
        }
    )
    assert any("BogusWorkload" in e for e in errors)


def test_valid_k8s_api_passes_by_api_map() -> None:
    a = _adapter_with_k8s_validator()
    for api in ("Pod", "Deployment", "StatefulSet", "Job"):
        assert (
            a.validate_template_config(
                {"template_id": "t", "image_id": "nginx", "provider_api": api}
            )
            == []
        )


def test_generic_missing_fields_still_flagged() -> None:
    a = _adapter_with_k8s_validator()
    errors = a.validate_template_config({"provider_api": "Pod"})
    # Missing template_id and image_id are generic checks.
    assert any("Template ID" in e for e in errors)
    assert any("Image ID" in e or "image_id" in e for e in errors)


def test_no_registry_falls_back_to_generic_only() -> None:
    a = TemplateConfigurationAdapter(template_manager=MagicMock(_registry=None), logger=MagicMock())
    # Bogus api not caught (no provider validator) but no crash — generic verdict.
    assert (
        a.validate_template_config(
            {
                "template_id": "t",
                "image_id": "nginx",
                "provider_api": "BogusWorkload",
                "provider_type": "k8s",
            }
        )
        == []
    )


def test_errors_deduplicated() -> None:
    a = _adapter_with_k8s_validator()
    # Missing image: generic check AND k8s validator both flag it → deduped list.
    errors = a.validate_template_config(
        {"template_id": "t", "provider_api": "Pod", "provider_type": "k8s"}
    )
    assert len(errors) == len(set(errors))


# ---------------------------------------------------------------------------
# Fix 1 regression: k8s-specific rules must fire (bad namespace, max_instances=0)
# ---------------------------------------------------------------------------


def test_bad_namespace_caught_via_adapter_delegation() -> None:
    """Regression: namespace 'INVALID_UPPER' must return errors, not [].

    Before the fix, validate_template_config built a generic Template that had
    no .namespace attribute, so the K8sTemplateValidator namespace rule never
    fired and the call returned [].
    """
    a = _adapter_with_k8s_validator()
    errors = a.validate_template_config(
        {
            "template_id": "t",
            "image_id": "nginx",
            "provider_api": "Pod",
            "provider_type": "k8s",
            "namespace": "INVALID_UPPER",
        }
    )
    assert errors, "Expected at least one error for an invalid namespace"
    assert any("namespace" in e.lower() for e in errors)


def test_max_instances_zero_caught_via_adapter_delegation() -> None:
    """Regression: max_instances=0 must return errors, not [].

    Before the fix, the generic Template default of 1 was used regardless of
    what the config dict supplied, hiding a zero/negative max_instances value.
    """
    a = _adapter_with_k8s_validator()
    errors = a.validate_template_config(
        {
            "template_id": "t",
            "image_id": "nginx",
            "provider_api": "Pod",
            "provider_type": "k8s",
            "max_instances": 0,
        }
    )
    assert errors, "Expected at least one error for max_instances=0"
    assert any("max_instances" in e for e in errors)


def test_k8s_namespace_forwarded_to_validator() -> None:
    """Valid namespace must still pass after the fix."""
    a = _adapter_with_k8s_validator()
    errors = a.validate_template_config(
        {
            "template_id": "t",
            "image_id": "nginx",
            "provider_api": "Pod",
            "provider_type": "k8s",
            "namespace": "my-namespace",
        }
    )
    assert errors == []


# ---------------------------------------------------------------------------
# Fix 2 regression: absent provider_api must not be an error for k8s
# ---------------------------------------------------------------------------


def test_absent_provider_api_not_error_for_k8s_by_provider_type() -> None:
    """Regression: a k8s template without provider_api must not get 'Provider API is required'.

    k8s defaults provider_api to Pod at acquire time, so a missing provider_api
    is valid for k8s templates.  Before the fix both the generic check and the
    k8s validator's empty-string rejection fired together.
    """
    a = _adapter_with_k8s_validator()
    errors = a.validate_template_config(
        {
            "template_id": "t",
            "image_id": "nginx",
            "provider_type": "k8s",
            # no provider_api key
        }
    )
    # Should not contain the generic "Provider API is required" error.
    assert not any("Provider API is required" in e for e in errors)


def test_absent_provider_api_is_error_for_aws() -> None:
    """provider_api is still required for AWS where there is no Pod default."""
    a = _adapter_with_k8s_validator()
    errors = a.validate_template_config(
        {
            "template_id": "t",
            "image_id": "ami-12345",
            "provider_type": "aws",
            # no provider_api key
        }
    )
    assert any("Provider API is required" in e for e in errors)
