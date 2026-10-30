"""Azure registration helpers: idempotence, failure handling and overwrite behaviour."""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry


@pytest.fixture(autouse=True)
def _restore_registries() -> Iterator[None]:
    ext_snapshot = dict(TemplateExtensionRegistry._extensions)
    cli_snapshot = CLISpecRegistry.all()
    mapping_snapshot = FieldMappingRegistry.all()
    yield
    TemplateExtensionRegistry._extensions.clear()
    TemplateExtensionRegistry._extensions.update(ext_snapshot)
    CLISpecRegistry.clear()
    for key, spec in cli_snapshot.items():
        CLISpecRegistry.register(key, spec)
    FieldMappingRegistry.clear()
    for key, mapping in mapping_snapshot.items():
        FieldMappingRegistry.register(key, mapping)


def _block_import(blocked: str):
    real_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == blocked:
            raise ImportError(f"blocked {name}")
        return real_import(name, *args, **kwargs)

    return patch("builtins.__import__", side_effect=_import)


class TestRegisterAzureExtensions:
    def test_existing_extension_is_kept(self):
        from orb.providers.azure.registration import register_azure_extensions

        class _SentinelExtension(BaseModel):
            pass

        TemplateExtensionRegistry.register_extension("azure", _SentinelExtension)

        register_azure_extensions()

        assert TemplateExtensionRegistry._extensions["azure"] is _SentinelExtension

    def test_failure_is_logged_with_traceback_and_reraised(self):
        from orb.providers.azure.registration import register_azure_extensions

        TemplateExtensionRegistry._extensions.pop("azure", None)
        logger = MagicMock()
        boom = ValueError("registry broken")

        with patch.object(TemplateExtensionRegistry, "register_extension", side_effect=boom):
            with pytest.raises(ValueError) as excinfo:
                register_azure_extensions(logger=logger)

        assert excinfo.value is boom
        logger.error.assert_called_once()
        assert "registry broken" in logger.error.call_args.args[0]
        assert logger.error.call_args.kwargs.get("exc_info") is True


class TestRegisterAzureCliSpec:
    def test_second_call_overwrites_existing_entry(self):
        from orb.providers.azure.cli.azure_cli_spec import AzureCLISpec
        from orb.providers.azure.registration import register_azure_cli_spec

        CLISpecRegistry.register("azure", MagicMock())

        register_azure_cli_spec()
        first = CLISpecRegistry.get("azure")
        register_azure_cli_spec()
        second = CLISpecRegistry.get("azure")

        assert isinstance(first, AzureCLISpec)
        assert isinstance(second, AzureCLISpec)
        assert second is not first

    def test_failure_is_wrapped_with_cause(self):
        from orb.providers.azure.registration import register_azure_cli_spec

        cause = ValueError("store broken")
        with patch.object(CLISpecRegistry, "register", side_effect=cause):
            with pytest.raises(RuntimeError, match="Failed to register Azure CLI spec") as excinfo:
                register_azure_cli_spec()

        assert excinfo.value.__cause__ is cause

    def test_import_error_is_skipped(self):
        from orb.providers.azure.registration import register_azure_cli_spec

        CLISpecRegistry._store.pop("azure", None)

        with _block_import("orb.providers.azure.cli.azure_cli_spec"):
            register_azure_cli_spec()

        assert CLISpecRegistry.get_or_none("azure") is None


class TestRegisterAzureHostfactoryFieldMapping:
    def test_second_call_overwrites_existing_entry(self):
        from orb.providers.azure.registration import register_azure_hostfactory_field_mapping
        from orb.providers.azure.scheduler.hostfactory_field_mapping import AzureFieldMapping

        FieldMappingRegistry.register("azure", MagicMock())

        register_azure_hostfactory_field_mapping()
        first = FieldMappingRegistry.get("azure")
        register_azure_hostfactory_field_mapping()
        second = FieldMappingRegistry.get("azure")

        assert isinstance(first, AzureFieldMapping)
        assert isinstance(second, AzureFieldMapping)
        assert second is not first

    def test_failure_is_wrapped_with_cause(self):
        from orb.providers.azure.registration import register_azure_hostfactory_field_mapping

        cause = ValueError("store broken")
        with patch.object(FieldMappingRegistry, "register", side_effect=cause):
            with pytest.raises(
                RuntimeError, match="Failed to register Azure HostFactory field mapping"
            ) as excinfo:
                register_azure_hostfactory_field_mapping()

        assert excinfo.value.__cause__ is cause

    def test_import_error_is_skipped(self):
        from orb.providers.azure.registration import register_azure_hostfactory_field_mapping

        FieldMappingRegistry._store.pop("azure", None)

        with _block_import("orb.providers.azure.scheduler.hostfactory_field_mapping"):
            register_azure_hostfactory_field_mapping()

        assert FieldMappingRegistry.get_or_none("azure") is None
