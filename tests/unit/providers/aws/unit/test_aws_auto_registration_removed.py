"""Regression test — importing orb.providers.aws.registration must NOT auto-register.

A fresh Python interpreter imports the module and checks that neither
register_aws_extensions nor register_aws_provider_settings was called as a
module-level side-effect.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

_CHECK_SCRIPT = """\
import sys

# Patch the registries to detect any call that happens at import time.
called = []

import orb.infrastructure.registry.template_extension_registry as ter
import orb.config.schemas.provider_settings_registry as psr

_orig_reg_ext = ter.TemplateExtensionRegistry.register_extension.__func__
_orig_reg_set = psr.ProviderSettingsRegistry.register_provider_settings.__func__

@classmethod
def _spy_ext(cls, provider_type, extension_class):
    called.append(('register_extension', provider_type))
    return _orig_reg_ext(cls, provider_type, extension_class)

@classmethod
def _spy_set(cls, provider_type, settings_class):
    called.append(('register_provider_settings', provider_type))
    return _orig_reg_set(cls, provider_type, settings_class)

ter.TemplateExtensionRegistry.register_extension = _spy_ext
psr.ProviderSettingsRegistry.register_provider_settings = _spy_set

# Import the module under test — this is where auto-reg used to fire.
import orb.providers.aws.registration  # noqa: F401

aws_calls = [c for c in called if c[1] == 'aws']
if aws_calls:
    print(f"FAIL: auto-registration fired at import time: {aws_calls}", file=sys.stderr)
    sys.exit(1)
else:
    print("OK: no auto-registration at import time")
    sys.exit(0)
"""


@pytest.mark.unit
def test_aws_auto_registration_not_fired_at_import() -> None:
    """Importing the aws registration module must not call register_aws_extensions
    or register_aws_provider_settings as a side-effect."""
    result = subprocess.run(
        [sys.executable, "-c", _CHECK_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Auto-registration detected at import time.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
