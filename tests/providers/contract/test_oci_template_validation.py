"""OCI template errors surface through the registry-backed validation adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

from orb.infrastructure.adapters.template_configuration_adapter import (
    TemplateConfigurationAdapter,
)


def _adapter(registry) -> TemplateConfigurationAdapter:
    manager = MagicMock()
    manager._registry = registry
    return TemplateConfigurationAdapter(template_manager=manager, logger=MagicMock())


def _config(**extra) -> dict:
    return {
        "template_id": "oci-1",
        "image_id": "ocid1.image.oc1..example",
        "provider_api": "OCICompute",
        "machine_types": {"VM.Standard.E6.Flex": 1},
        **extra,
    }


def test_invalid_capacity_type_is_reported(registry) -> None:
    errors = _adapter(registry).validate_template_config(_config(capacity_type="spot"))

    assert any("capacity_type" in e for e in errors)


def test_valid_template_has_no_errors(registry) -> None:
    assert _adapter(registry).validate_template_config(_config()) == []
