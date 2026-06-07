"""Unit tests for TemplateConfigurationAdapter validation behavior."""

from unittest.mock import Mock

from orb.infrastructure.adapters.template_configuration_adapter import TemplateConfigurationAdapter


def _make_adapter() -> TemplateConfigurationAdapter:
    template_manager = Mock()
    logger = Mock()
    return TemplateConfigurationAdapter(template_manager=template_manager, logger=logger)


def test_validate_template_config_requires_provider_api() -> None:
    adapter = _make_adapter()

    errors = adapter.validate_template_config(
        {
            "template_id": "tpl-1",
            "image_id": "img-1",
        }
    )

    assert "Provider API is required" in errors


def test_validate_template_config_still_requires_image_id() -> None:
    adapter = _make_adapter()

    errors = adapter.validate_template_config(
        {
            "template_id": "tpl-1",
        }
    )

    assert "Image ID is required" in errors
