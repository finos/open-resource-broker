"""Unit tests for early-bootstrap ordering in provider CLI-spec and defaults-loader registration.

Verifies that :func:`register_all_provider_cli_specs` and
:func:`register_all_defaults_loaders` both call
:func:`discover_provider_plugins` as their *first* action so that
entry-point providers are visible before the iteration loop runs.

This is the ordering trap documented in the plan: ``cli/args.py`` calls
``register_all_provider_cli_specs()`` pre-bootstrap, so the list must be
populated by entry-point discovery at that point.
"""

from __future__ import annotations

from unittest.mock import patch


def test_register_all_provider_cli_specs_calls_discover_first(monkeypatch) -> None:
    """register_all_provider_cli_specs calls discover_provider_plugins before iterating."""
    # Import the module before patching so sys.modules holds the canonical object.
    # A prior test may evict `orb.providers.registration` from sys.modules; if
    # monkeypatch.setattr runs while the module is absent it patches a stale
    # package-attribute object (the `orb.providers.registration` binding left on the
    # orb.providers package), while the subsequent `import` statement re-imports a
    # fresh copy — two different objects, so the patch is invisible at call time.
    import orb.providers.registration as reg_mod

    call_log: list[str] = []

    def _fake_discover(*args, **kwargs):
        call_log.append("discover")
        return []

    monkeypatch.setattr("orb.providers.registration.discover_provider_plugins", _fake_discover)
    # Start with an empty provider list to ensure discover is truly first
    monkeypatch.setattr("orb.providers.registration._REGISTERED_PROVIDERS", [])

    reg_mod.register_all_provider_cli_specs()

    assert call_log, "discover_provider_plugins must be called"
    assert call_log[0] == "discover", (
        "discover_provider_plugins must be the FIRST call in register_all_provider_cli_specs"
    )


def test_register_all_defaults_loaders_calls_discover_first(monkeypatch) -> None:
    """register_all_defaults_loaders calls discover_provider_plugins before iterating."""
    # Import before patching for the same reason as the cli-specs test above:
    # ensures sys.modules holds the live module so monkeypatch and the
    # registration call operate on the same object regardless of suite order.
    import orb.providers.registration as reg_mod

    call_log: list[str] = []

    def _fake_discover(*args, **kwargs):
        call_log.append("discover")
        return []

    monkeypatch.setattr("orb.providers.registration.discover_provider_plugins", _fake_discover)
    monkeypatch.setattr("orb.providers.registration._REGISTERED_PROVIDERS", [])

    reg_mod.register_all_defaults_loaders()

    assert call_log, "discover_provider_plugins must be called"
    assert call_log[0] == "discover", (
        "discover_provider_plugins must be the FIRST call in register_all_defaults_loaders"
    )


def test_register_all_provider_cli_specs_with_patched_entry_points_populates_registry(
    monkeypatch,
) -> None:
    """Entry-point plugin that appends to _REGISTERED_PROVIDERS is visible after discover.

    Uses monkeypatch to replace the inner loop so the fake provider name does
    not trigger ``importlib.util.find_spec`` on a non-existent module path.
    """
    import importlib.metadata

    import orb.providers.registration as reg_mod

    original_providers = list(reg_mod._REGISTERED_PROVIDERS)
    plugins_called: list[str] = []

    class _FakeEP:
        name = "testprovider"

        def load(self):
            def _fn():
                plugins_called.append("testprovider")
                reg_mod._REGISTERED_PROVIDERS.append("testprovider")

            return _fn

    # Patch the registry-iteration part so we only verify discovery happened
    monkeypatch.setattr(
        "orb.infrastructure.registry.cli_spec_registry.CLISpecRegistry.get_or_none",
        lambda name: object(),  # pretend all already registered → loop skips
    )

    with patch.object(importlib.metadata, "entry_points", return_value=[_FakeEP()]):
        reg_mod._REGISTERED_PROVIDERS.clear()
        try:
            reg_mod.register_all_provider_cli_specs()
            # The fake plugin ran and appended its name
            assert "testprovider" in plugins_called
            assert "testprovider" in reg_mod._REGISTERED_PROVIDERS
        finally:
            reg_mod._REGISTERED_PROVIDERS.clear()
            reg_mod._REGISTERED_PROVIDERS.extend(original_providers)
