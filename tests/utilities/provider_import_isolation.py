"""Shared test helper for verifying provider registration modules do not auto-register on import."""

from __future__ import annotations

import subprocess
import sys

_IMPORT_ISOLATION_SCRIPT = """\
import importlib
import sys

module_name = sys.argv[1]
provider_type = sys.argv[2]

called = []

import orb.config.schemas.provider_settings_registry as psr
import orb.infrastructure.registry.cli_spec_registry as csr
import orb.infrastructure.registry.template_extension_registry as ter
import orb.infrastructure.scheduler.hostfactory.field_mapping_registry as fmr
import orb.providers.registry.defaults_loader_registry as dlr

_orig_reg_ext = ter.TemplateExtensionRegistry.register_extension.__func__
_orig_reg_set = psr.ProviderSettingsRegistry.register_provider_settings.__func__
_orig_reg_cli = csr.CLISpecRegistry.register.__func__
_orig_reg_fm = fmr.FieldMappingRegistry.register.__func__
_orig_reg_dl = dlr.DefaultsLoaderRegistry.register.__func__


@classmethod
def _spy_ext(cls, ptype, extension_class):
    called.append(("TemplateExtensionRegistry.register_extension", ptype))
    return _orig_reg_ext(cls, ptype, extension_class)


@classmethod
def _spy_set(cls, ptype, settings_class):
    called.append(("ProviderSettingsRegistry.register_provider_settings", ptype))
    return _orig_reg_set(cls, ptype, settings_class)


@classmethod
def _spy_cli(cls, key, value):
    called.append(("CLISpecRegistry.register", key))
    return _orig_reg_cli(cls, key, value)


@classmethod
def _spy_fm(cls, key, value):
    called.append(("FieldMappingRegistry.register", key))
    return _orig_reg_fm(cls, key, value)


@classmethod
def _spy_dl(cls, key, value):
    called.append(("DefaultsLoaderRegistry.register", key))
    return _orig_reg_dl(cls, key, value)


ter.TemplateExtensionRegistry.register_extension = _spy_ext
psr.ProviderSettingsRegistry.register_provider_settings = _spy_set
csr.CLISpecRegistry.register = _spy_cli
fmr.FieldMappingRegistry.register = _spy_fm
dlr.DefaultsLoaderRegistry.register = _spy_dl

importlib.import_module(module_name)

provider_calls = [c for c in called if c[1] == provider_type]
if provider_calls:
    print(
        f"FAIL: auto-registration fired at import time for {provider_type}: {provider_calls}",
        file=sys.stderr,
    )
    sys.exit(1)
sys.exit(0)
"""


def assert_no_auto_registration_on_import(module_name: str, provider_type: str) -> None:
    """Import ``module_name`` in a fresh Python interpreter and assert zero satellite registrations.

    Spies on ``TemplateExtensionRegistry.register_extension``,
    ``ProviderSettingsRegistry.register_provider_settings``,
    ``CLISpecRegistry.register``, ``FieldMappingRegistry.register``, and
    ``DefaultsLoaderRegistry.register``.
    """
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_ISOLATION_SCRIPT, module_name, provider_type],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"Auto-registration detected at import time for {provider_type} ({module_name}).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
