"""``fakecr`` registers through ORB's own bootstrap loops and leaves nothing behind."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.application.services.template_defaults_service import TemplateDefaultsService
from orb.bootstrap.provider_completeness import assert_provider_registrations_complete
from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
from orb.infrastructure.registry.template_example_generator_registry import (
    TemplateExampleGeneratorRegistry,
)
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
from orb.providers import registration as provider_registration
from orb.providers.base import provider_plugin
from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry
from tests.providers.contract.fresh_registry import freshly_registered_providers
from tests.providers.contract.test_provider_conformance import KNOWN_PROVIDERS
from tests.providers.k8s.extension.fakecr_provider import (
    PROVIDER_TYPE,
    FakeCrDefaultsLoader,
    FakeCrStrategy,
    FakeCrTemplate,
)
from tests.providers.k8s.extension.isolation import bootstrapped_with_fakecr

pytestmark = pytest.mark.unit


def test_bootstrap_registers_fakecr_next_to_the_real_providers() -> None:
    with bootstrapped_with_fakecr() as booted:
        types_ = set(booted.registry.get_registered_types())
        assert PROVIDER_TYPE in types_
        assert {"aws", "k8s"} <= types_
        assert_provider_registrations_complete()
        registration = booted.registry._get_type_registration(PROVIDER_TYPE)
        assert registration.strategy_class is FakeCrStrategy  # type: ignore[attr-defined]
        assert booted.registry.get_default_api(PROVIDER_TYPE) == "Widget"
        assert booted.template_factory.get_registered_template_classes()[PROVIDER_TYPE] is (
            FakeCrTemplate
        )


def test_every_satellite_registry_gets_an_entry() -> None:
    with bootstrapped_with_fakecr():
        assert CLISpecRegistry.get_or_none(PROVIDER_TYPE) is not None
        assert FieldMappingRegistry.get_or_none(PROVIDER_TYPE) is not None
        assert isinstance(DefaultsLoaderRegistry.get(PROVIDER_TYPE), FakeCrDefaultsLoader)
        assert TemplateExampleGeneratorRegistry.get_or_none(PROVIDER_TYPE) is not None
        assert TemplateExtensionRegistry.has_extension(PROVIDER_TYPE)
        assert ProviderSettingsRegistry.get_or_none(PROVIDER_TYPE) is not None


def test_nothing_leaks_after_the_context_exits() -> None:
    registered = list(provider_registration._REGISTERED_PROVIDERS)
    initialized = set(provider_plugin._initialized_providers)
    stores = {
        cls: dict(cls._store)  # type: ignore[attr-defined]
        for cls in (
            CLISpecRegistry,
            FieldMappingRegistry,
            DefaultsLoaderRegistry,
            TemplateExampleGeneratorRegistry,
        )
    }
    extensions = dict(TemplateExtensionRegistry._extensions)
    settings = dict(ProviderSettingsRegistry._settings_classes)
    with bootstrapped_with_fakecr():
        pass
    assert provider_registration._REGISTERED_PROVIDERS == registered
    assert provider_plugin._initialized_providers == initialized
    for cls, store in stores.items():
        assert cls._store == store  # type: ignore[attr-defined]
    assert TemplateExtensionRegistry._extensions == extensions
    assert ProviderSettingsRegistry._settings_classes == settings
    assert f"orb.providers.{PROVIDER_TYPE}" not in sys.modules
    assert f"orb.providers.{PROVIDER_TYPE}.registration" not in sys.modules


def test_registry_afterwards_yields_only_the_known_providers() -> None:
    with bootstrapped_with_fakecr():
        pass
    with freshly_registered_providers() as registry:
        assert sorted(registry.get_registered_providers()) == sorted(KNOWN_PROVIDERS)


def test_state_is_restored_when_the_body_raises() -> None:
    raised = False
    try:
        with bootstrapped_with_fakecr():
            raise RuntimeError("boom")
    except RuntimeError:
        raised = True
    assert raised
    assert PROVIDER_TYPE not in provider_registration._REGISTERED_PROVIDERS
    assert not TemplateExtensionRegistry.has_extension(PROVIDER_TYPE)


def test_row_without_provider_type_becomes_a_fakecr_template() -> None:
    row = {"template_id": "w1", "namespace": "team-a"}
    with bootstrapped_with_fakecr() as booted:
        provider_defaults = DefaultsLoaderRegistry.get(PROVIDER_TYPE).load_defaults()["provider"][
            "provider_defaults"
        ][PROVIDER_TYPE]
        config_manager = MagicMock()
        config_manager.get_template_config.return_value = {}
        config_manager.get_provider_config.return_value = SimpleNamespace(
            providers=[
                SimpleNamespace(name="fakecr_ctx", type=PROVIDER_TYPE, template_defaults={})
            ],
            provider_defaults={
                PROVIDER_TYPE: SimpleNamespace(
                    template_defaults=provider_defaults["template_defaults"]
                )
            },
        )
        service = TemplateDefaultsService(config_manager=config_manager, logger=MagicMock())
        resolved = service.resolve_template_defaults(row, "fakecr_ctx")
        template = booted.template_factory.create_template(resolved)
    assert isinstance(template, FakeCrTemplate)
    assert template.provider_type == PROVIDER_TYPE
    assert template.namespace == "team-a"
    assert template.provider_api == "Widget"
