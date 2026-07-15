"""Import guards for Azure optional runtime packages."""

from __future__ import annotations

import sys
from contextlib import contextmanager

_MISSING_AZURE_MODULES = {
    "azure": None,
    "azure.core": None,
    "azure.core.exceptions": None,
    "azure.identity": None,
    "azure.mgmt": None,
    "azure.mgmt.compute": None,
    "azure.mgmt.network": None,
    "azure.mgmt.resource": None,
    "azure.mgmt.resource.subscriptions": None,
}


@contextmanager
def _isolated_azure_provider_import():
    """Re-import Azure provider modules with Azure SDK packages hidden."""
    saved_orb_modules = {
        k: v for k, v in sys.modules.items() if k.startswith("orb.providers.azure")
    }
    saved_azure_modules = {k: sys.modules[k] for k in _MISSING_AZURE_MODULES if k in sys.modules}

    for key in list(sys.modules):
        if key.startswith("orb.providers.azure"):
            del sys.modules[key]

    for key, value in _MISSING_AZURE_MODULES.items():
        # None is Python's import-blocking sentinel, omitted from typeshed's module map.
        sys.modules[key] = value  # type: ignore[assignment]

    try:
        yield
    finally:
        for key in list(sys.modules):
            if key.startswith("orb.providers.azure"):
                del sys.modules[key]
        for key in _MISSING_AZURE_MODULES:
            if key in saved_azure_modules:
                sys.modules[key] = saved_azure_modules[key]
            else:
                sys.modules.pop(key, None)
        sys.modules.update(saved_orb_modules)
        for module_name, module in sorted(
            saved_orb_modules.items(), key=lambda item: item[0].count(".")
        ):
            parent_name, attribute_name = module_name.rsplit(".", maxsplit=1)
            parent_module = sys.modules.get(parent_name)
            if parent_module is not None:
                setattr(parent_module, attribute_name, module)


def test_azure_package_import_does_not_require_azure_sdk() -> None:
    with _isolated_azure_provider_import():
        import orb.providers.azure as azure_provider

        assert azure_provider is not None


def test_azure_registration_import_does_not_require_azure_sdk() -> None:
    with _isolated_azure_provider_import():
        from orb.providers.azure.registration import register_azure_provider

        assert callable(register_azure_provider)


def test_register_azure_provider_does_not_require_azure_sdk() -> None:
    with _isolated_azure_provider_import():
        from orb.providers.azure.registration import register_azure_provider
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        registry.clear_registrations()

        register_azure_provider(registry=registry)

        assert registry.is_provider_registered("azure") is True


def test_create_azure_strategy_does_not_require_azure_sdk_until_runtime() -> None:
    with _isolated_azure_provider_import():
        from orb.providers.azure.registration import create_azure_strategy

        strategy = create_azure_strategy(
            {
                "subscription_id": "12345678-1234-1234-1234-123456789012",
                "resource_group": "orb-rg",
                "region": "eastus2",
            },
            provider_instance_name="azure-default",
        )

        assert strategy.is_initialized is False
