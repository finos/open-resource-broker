"""Verify ORB core boots cleanly when the [aws] extra is not installed.

These tests use subprocess + import mocking to simulate a boto3-free environment
without requiring a separate virtualenv.  The approach patches sys.modules to make
boto3/botocore appear absent, then imports ORB core modules under that constraint.

Marked @pytest.mark.slow because they manipulate sys.modules and must restore state.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Generator
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ImportBlocker(ModuleType):
    """A fake module that raises ImportError on attribute access, simulating absence."""

    def __getattr__(self, name: str) -> object:
        raise ImportError(f"boto3 extra not installed (simulated)")


def _block_modules(*names: str) -> dict[str, ModuleType | None]:
    """Return a dict suitable for patching sys.modules to block *names*."""
    return {name: None for name in names}  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_cleanup_schema_import_without_aws() -> None:
    """cleanup_schema must import without error even when boto3 is absent."""
    blocked = {
        "boto3": None,
        "botocore": None,
        "orb.providers.aws.configuration.cleanup_config": None,
    }
    # Remove cached module if present
    cached = {k: sys.modules.pop(k, None) for k in blocked}
    cached.pop("orb.config.schemas.cleanup_schema", None)
    sys.modules.pop("orb.config.schemas.cleanup_schema", None)

    try:
        with patch.dict(sys.modules, blocked):
            import importlib

            mod = importlib.import_module("orb.config.schemas.cleanup_schema")
            # CleanupConfig should be None when AWS extra is absent
            assert mod.CleanupConfig is None, (
                "CleanupConfig should be None when [aws] extra is absent"
            )
            assert mod.CleanupResourcesConfig is None, (
                "CleanupResourcesConfig should be None when [aws] extra is absent"
            )
    finally:
        # Restore originals so other tests are unaffected
        for k, v in cached.items():
            if v is not None:
                sys.modules[k] = v
            else:
                sys.modules.pop(k, None)
        sys.modules.pop("orb.config.schemas.cleanup_schema", None)


@pytest.mark.slow
def test_storage_registration_without_aws() -> None:
    """register_all_storage_types must not raise when AWS DynamoDB import fails."""
    blocked = {
        "boto3": None,
        "botocore": None,
        "orb.providers.aws.storage.registration": None,
    }
    cached = {k: sys.modules.pop(k, None) for k in blocked}
    sys.modules.pop("orb.infrastructure.storage.registration", None)

    try:
        with patch.dict(sys.modules, blocked):
            mod = importlib.import_module("orb.infrastructure.storage.registration")
            # Should not raise — DynamoDB registration is silently skipped
            try:
                mod.register_all_storage_types()
            except ImportError as exc:
                pytest.fail(
                    f"register_all_storage_types() raised ImportError when [aws] absent: {exc}"
                )
    finally:
        for k, v in cached.items():
            if v is not None:
                sys.modules[k] = v
            else:
                sys.modules.pop(k, None)
        sys.modules.pop("orb.infrastructure.storage.registration", None)


@pytest.mark.slow
def test_provider_cli_specs_without_aws() -> None:
    """register_all_provider_cli_specs must not raise when [aws] is absent."""
    blocked = {
        "boto3": None,
        "botocore": None,
        "orb.providers.aws.cli.aws_cli_spec": None,
    }
    cached = {k: sys.modules.pop(k, None) for k in blocked}
    sys.modules.pop("orb.providers.registration", None)

    try:
        with patch.dict(sys.modules, blocked):
            mod = importlib.import_module("orb.providers.registration")
            try:
                mod.register_all_provider_cli_specs()
            except ImportError as exc:
                pytest.fail(
                    f"register_all_provider_cli_specs() raised ImportError when [aws] absent: {exc}"
                )
    finally:
        for k, v in cached.items():
            if v is not None:
                sys.modules[k] = v
            else:
                sys.modules.pop(k, None)
        sys.modules.pop("orb.providers.registration", None)


@pytest.mark.slow
def test_provider_defaults_loaders_without_aws() -> None:
    """register_all_defaults_loaders must not raise when [aws] is absent."""
    blocked = {
        "boto3": None,
        "botocore": None,
        "orb.providers.aws.defaults_loader": None,
    }
    cached = {k: sys.modules.pop(k, None) for k in blocked}
    sys.modules.pop("orb.providers.registration", None)

    try:
        with patch.dict(sys.modules, blocked):
            mod = importlib.import_module("orb.providers.registration")
            try:
                mod.register_all_defaults_loaders()
            except ImportError as exc:
                pytest.fail(
                    f"register_all_defaults_loaders() raised ImportError when [aws] absent: {exc}"
                )
    finally:
        for k, v in cached.items():
            if v is not None:
                sys.modules[k] = v
            else:
                sys.modules.pop(k, None)
        sys.modules.pop("orb.providers.registration", None)
