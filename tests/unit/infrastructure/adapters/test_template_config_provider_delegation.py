"""Tests that the generic template validator delegates to provider rules.

The CLI `orb templates validate` path reaches
TemplateConfigurationAdapter.validate_template_config.  It must run the active
provider's registered validator so provider-specific rules (e.g. the k8s
validator rejecting an unknown provider_api) apply — not just the generic
present/absent field checks.
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
    assert any("Image ID" in e.title() or "image_id" in e for e in errors)


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
