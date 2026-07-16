"""Unit tests — register_aws_extensions and register_aws_provider_settings are idempotent.

Calling each function a second time must be a no-op: no exception, no duplicate
registration, and registry state unchanged from the first call.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_registries():
    """Save/restore both registries around each test so state does not leak."""
    from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
    from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry

    saved_settings = dict(ProviderSettingsRegistry._settings_classes)
    saved_extensions = dict(TemplateExtensionRegistry._extensions)
    saved_instances = dict(TemplateExtensionRegistry._extension_instances)

    # Start clean for isolation
    ProviderSettingsRegistry._settings_classes.clear()
    TemplateExtensionRegistry._extensions.clear()
    TemplateExtensionRegistry._extension_instances.clear()

    yield

    ProviderSettingsRegistry._settings_classes.clear()
    ProviderSettingsRegistry._settings_classes.update(saved_settings)
    TemplateExtensionRegistry._extensions.clear()
    TemplateExtensionRegistry._extensions.update(saved_extensions)
    TemplateExtensionRegistry._extension_instances.clear()
    TemplateExtensionRegistry._extension_instances.update(saved_instances)


@pytest.mark.unit
def test_register_aws_extensions_idempotent_no_error() -> None:
    """Calling register_aws_extensions twice must not raise."""
    from orb.providers.aws.registration import register_aws_extensions

    register_aws_extensions()
    register_aws_extensions()  # second call must be a no-op, not an error


@pytest.mark.unit
def test_register_aws_extensions_idempotent_same_class() -> None:
    """Second call must leave the same extension class registered."""
    from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
    from orb.providers.aws.domain.template.aws_template_dto_config import AWSTemplateDTOConfig
    from orb.providers.aws.registration import register_aws_extensions

    register_aws_extensions()
    first_class = TemplateExtensionRegistry.get_extension_class("aws")

    register_aws_extensions()
    second_class = TemplateExtensionRegistry.get_extension_class("aws")

    assert first_class is AWSTemplateDTOConfig
    assert second_class is first_class


@pytest.mark.unit
def test_register_aws_provider_settings_idempotent_no_error() -> None:
    """Calling register_aws_provider_settings twice must not raise."""
    from orb.providers.aws.registration import register_aws_provider_settings

    register_aws_provider_settings()
    register_aws_provider_settings()  # second call must be a no-op, not an error


@pytest.mark.unit
def test_register_aws_provider_settings_idempotent_same_class() -> None:
    """Second call must leave the same settings class registered."""
    from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
    from orb.providers.aws.configuration.config import AWSProviderConfig
    from orb.providers.aws.registration import register_aws_provider_settings

    register_aws_provider_settings()
    first_class = ProviderSettingsRegistry.get_or_none("aws")

    register_aws_provider_settings()
    second_class = ProviderSettingsRegistry.get_or_none("aws")

    assert first_class is AWSProviderConfig
    assert second_class is first_class
