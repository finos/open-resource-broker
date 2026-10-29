"""Keep OCI defaults registration local to each test."""

import pytest

from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry


@pytest.fixture(autouse=True)
def isolate_defaults_registration(monkeypatch):
    """A partial OCI registration must not affect another provider's tests."""
    monkeypatch.setattr(DefaultsLoaderRegistry, "_store", DefaultsLoaderRegistry.all())


@pytest.fixture(autouse=True)
def mocked_cli_presence(monkeypatch):
    """Unit factories do not depend on a developer's OCI CLI installation."""
    import shutil

    original = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: "oci" if name == "oci" else original(name))
