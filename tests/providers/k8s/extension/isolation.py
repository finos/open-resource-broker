"""Bootstrap the real providers plus ``fakecr`` through ORB's own loops, then undo all of it.

The provider registry is a process-wide singleton and so are the satellite
registries, the discovery list and the initialised-provider guard.  Everything
the bootstrap loops write is restored on exit, so nothing leaks into a test
that expects only the known providers on the same xdist worker.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

from orb.bootstrap import infrastructure_services
from orb.bootstrap.provider_services import _register_provider_utility_services
from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
from orb.infrastructure.registry.template_example_generator_registry import (
    TemplateExampleGeneratorRegistry,
)
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
from orb.providers import registration as provider_registration
from orb.providers.base import provider_plugin
from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry
from orb.providers.registry.provider_registry import ProviderRegistry
from tests.providers.contract.fresh_registry import freshly_registered_providers
from tests.providers.k8s.extension.fakecr_provider import (
    PROVIDER_TYPE,
    build_package_module,
    build_registration_module,
)

_STORES: tuple[type, ...] = (
    CLISpecRegistry,
    FieldMappingRegistry,
    DefaultsLoaderRegistry,
    TemplateExampleGeneratorRegistry,
)
_MODULE_NAMES = (
    f"orb.providers.{PROVIDER_TYPE}",
    f"orb.providers.{PROVIDER_TYPE}.registration",
)


@dataclass
class Bootstrapped:
    registry: ProviderRegistry
    template_factory: TemplateFactory


def _snapshot() -> dict[str, Any]:
    return {
        "stores": [(cls, dict(cls._store)) for cls in _STORES],  # type: ignore[attr-defined]
        "extensions": dict(TemplateExtensionRegistry._extensions),
        "extension_instances": dict(TemplateExtensionRegistry._extension_instances),
        "settings": dict(ProviderSettingsRegistry._settings_classes),
        "registered": list(provider_registration._REGISTERED_PROVIDERS),
        "initialized": set(provider_plugin._initialized_providers),
        "modules": {name: sys.modules.get(name) for name in _MODULE_NAMES},
    }


def _restore(saved: dict[str, Any]) -> None:
    for cls, store in saved["stores"]:
        cls._store.clear()
        cls._store.update(store)
    TemplateExtensionRegistry._extensions.clear()
    TemplateExtensionRegistry._extensions.update(saved["extensions"])
    TemplateExtensionRegistry._extension_instances.clear()
    TemplateExtensionRegistry._extension_instances.update(saved["extension_instances"])
    ProviderSettingsRegistry._settings_classes.clear()
    ProviderSettingsRegistry._settings_classes.update(saved["settings"])
    provider_registration._REGISTERED_PROVIDERS[:] = saved["registered"]
    provider_plugin._initialized_providers.clear()
    provider_plugin._initialized_providers.update(saved["initialized"])
    for name, module in saved["modules"].items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _build_template_factory() -> TemplateFactory:
    """Run bootstrap's own ``TemplateFactory`` singleton factory against the live registry."""
    container = MagicMock()
    infrastructure_services._register_template_services(container)
    factory_function = next(
        call.args[1]
        for call in container.register_singleton.call_args_list
        if call.args and call.args[0] is TemplateFactory
    )
    factory = factory_function(MagicMock())
    assert isinstance(factory, TemplateFactory)
    return factory


@contextmanager
def bootstrapped_with_fakecr() -> Iterator[Bootstrapped]:
    """Register every real provider and ``fakecr`` through bootstrap's loops."""
    saved = _snapshot()
    sys.modules[_MODULE_NAMES[0]] = build_package_module()
    sys.modules[_MODULE_NAMES[1]] = build_registration_module()
    if PROVIDER_TYPE not in provider_registration._REGISTERED_PROVIDERS:
        provider_registration._REGISTERED_PROVIDERS.append(PROVIDER_TYPE)
    try:
        # ``register_all_providers`` runs inside the context: it registers the
        # real providers by discovery and ``fakecr`` from the module above.
        with freshly_registered_providers() as registry:
            # The satellite registries, DI services and template factory are
            # populated by the same loops the application runs at startup.
            _register_provider_utility_services(MagicMock())
            yield Bootstrapped(registry, _build_template_factory())
    finally:
        _restore(saved)
