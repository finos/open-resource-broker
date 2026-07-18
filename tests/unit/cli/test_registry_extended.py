"""Extended tests for orb.cli.registry.

Covers register, lookup (with aliases), the _ALIASES map, and idempotent
build_registry() behaviour — without importing unrelated handler modules.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
class TestRegisterAndLookup:
    """register() and lookup() without going through build_registry()."""

    def _fresh_state(self):
        """Return a clean copy of _REGISTRY for test isolation."""
        import orb.cli.registry as reg

        # Snapshot current state so we can restore it
        return dict(reg._REGISTRY)

    def test_register_then_lookup_returns_handler(self):
        import orb.cli.registry as reg

        snapshot = dict(reg._REGISTRY)
        try:
            sentinel = lambda args: "sentinel"  # noqa: E731
            reg.register("testresource-xyz", "testaction-xyz", sentinel)
            assert reg.lookup("testresource-xyz", "testaction-xyz") is sentinel
        finally:
            reg._REGISTRY.clear()
            reg._REGISTRY.update(snapshot)

    def test_lookup_returns_none_for_unknown(self):
        import orb.cli.registry as reg

        assert reg.lookup("definitely-not-a-resource", "no-action") is None

    def test_lookup_resolves_singular_machine_alias(self):
        import orb.cli.registry as reg

        build_registry = reg.build_registry
        build_registry()
        # "machine" → "machines"
        h1 = reg.lookup("machines", "list")
        h2 = reg.lookup("machine", "list")
        assert h1 is not None
        assert h1 is h2

    def test_lookup_resolves_template_alias(self):
        import orb.cli.registry as reg

        reg.build_registry()
        h1 = reg.lookup("templates", "show")
        h2 = reg.lookup("template", "show")
        assert h1 is not None
        assert h1 is h2

    def test_lookup_resolves_request_alias(self):
        import orb.cli.registry as reg

        reg.build_registry()
        h1 = reg.lookup("requests", "list")
        h2 = reg.lookup("request", "list")
        assert h1 is h2

    def test_lookup_resolves_provider_alias(self):
        import orb.cli.registry as reg

        reg.build_registry()
        h1 = reg.lookup("providers", "list")
        h2 = reg.lookup("provider", "list")
        assert h1 is h2

    def test_lookup_resolves_infra_alias(self):
        import orb.cli.registry as reg

        reg.build_registry()
        h1 = reg.lookup("infrastructure", "discover")
        h2 = reg.lookup("infra", "discover")
        assert h1 is h2


@pytest.mark.unit
class TestBuildRegistryIdempotent:
    def test_second_call_does_not_raise(self):
        import orb.cli.registry as reg

        reg.build_registry()
        reg.build_registry()  # Must not raise

    def test_registry_non_empty_after_build(self):
        import orb.cli.registry as reg

        reg.build_registry()
        assert len(reg._REGISTRY) > 0


@pytest.mark.unit
class TestMachineRegistrations:
    """Spot-check that key machine (resource, action) pairs are registered."""

    def setup_method(self):
        import orb.cli.registry as reg

        reg.build_registry()

    def test_machines_list_registered(self):
        from orb.cli.registry import lookup

        assert lookup("machines", "list") is not None

    def test_machines_show_registered(self):
        from orb.cli.registry import lookup

        assert lookup("machines", "show") is not None

    def test_machines_stop_registered(self):
        from orb.cli.registry import lookup

        assert lookup("machines", "stop") is not None

    def test_machines_start_registered(self):
        from orb.cli.registry import lookup

        assert lookup("machines", "start") is not None

    def test_machines_return_registered(self):
        from orb.cli.registry import lookup

        assert lookup("machines", "return") is not None


@pytest.mark.unit
class TestProviderRegistrations:
    def setup_method(self):
        import orb.cli.registry as reg

        reg.build_registry()

    def test_providers_list_registered(self):
        from orb.cli.registry import lookup

        assert lookup("providers", "list") is not None

    def test_providers_health_registered(self):
        from orb.cli.registry import lookup

        assert lookup("providers", "health") is not None

    def test_providers_metrics_registered(self):
        from orb.cli.registry import lookup

        assert lookup("providers", "metrics") is not None
