"""Unit tests for :class:`orb.providers.base.provider_plugin.ProviderPlugin`.

Covers:
- Abstract-method enforcement (subclasses missing satellites cannot be instantiated)
- Idempotent :meth:`initialize_provider` (second call is a safe no-op)
- Failure clears the guard (failed init does not poison the guard set)
- :meth:`register_plugin` classmethod wires the provider registry and appends
  the provider name to ``_REGISTERED_PROVIDERS``
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.base.provider_plugin import ProviderPlugin, reset_for_testing

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_concrete(
    name: str = "test",
    *,
    fail_on_init: bool = False,
) -> type[ProviderPlugin]:
    """Return a fully concrete ProviderPlugin subclass for testing.

    All satellite accessors return lightweight stubs so the orchestrated
    lifecycle can run without any real provider infrastructure.
    """

    class _ConcretePlugin(ProviderPlugin):
        provider_name = name

        def strategy_factory(self) -> Any:
            return lambda cfg: MagicMock(name="strategy")

        def config_factory(self) -> Any:
            return lambda data: MagicMock(name="config")

        def template_dto_config(self) -> Any:
            return None  # no DTO extension for these tests

        def cli_spec(self) -> Any:
            return None  # skip CLI registration

        def field_mapping(self) -> Any:
            return None  # skip field-mapping registration

        def defaults_loader(self) -> Any:
            return None  # skip defaults-loader registration

        def template_example_generator(self, container: Any) -> Any:
            return None  # skip generator registration

        def register_auth_strategies(self, logger: Optional[Any] = None) -> None:
            if fail_on_init:
                raise RuntimeError("deliberate auth-strategy failure")

    return _ConcretePlugin


# ---------------------------------------------------------------------------
# Abstract-method enforcement
# ---------------------------------------------------------------------------


class TestAbstractEnforcement:
    """Verify that incomplete subclasses cannot be instantiated."""

    def test_cannot_instantiate_abstract_base_directly(self) -> None:
        """ProviderPlugin itself is abstract and must not be instantiable."""
        with pytest.raises(TypeError):
            ProviderPlugin()  # type: ignore[abstract]

    def test_missing_strategy_factory_raises(self) -> None:
        """A subclass missing ``strategy_factory`` cannot be instantiated."""

        class _Incomplete(ProviderPlugin):
            provider_name = "incomplete"

            # strategy_factory intentionally omitted

            def config_factory(self) -> Any:
                return None

            def template_dto_config(self) -> Any:
                return None

            def cli_spec(self) -> Any:
                return None

            def field_mapping(self) -> Any:
                return None

            def defaults_loader(self) -> Any:
                return None

            def template_example_generator(self, container: Any) -> Any:
                return None

        with pytest.raises(TypeError):
            _Incomplete()

    def test_missing_defaults_loader_raises(self) -> None:
        """A subclass missing ``defaults_loader`` cannot be instantiated."""

        class _Incomplete(ProviderPlugin):
            provider_name = "incomplete"

            def strategy_factory(self) -> Any:
                return None

            def config_factory(self) -> Any:
                return None

            def template_dto_config(self) -> Any:
                return None

            def cli_spec(self) -> Any:
                return None

            def field_mapping(self) -> Any:
                return None

            # defaults_loader intentionally omitted

            def template_example_generator(self, container: Any) -> Any:
                return None

        with pytest.raises(TypeError):
            _Incomplete()

    def test_fully_concrete_subclass_instantiates(self) -> None:
        """A subclass that implements all abstract methods can be instantiated."""
        cls = _make_concrete("fully_concrete")
        plugin = cls()
        assert plugin.provider_name == "fully_concrete"


# ---------------------------------------------------------------------------
# initialize_provider idempotency
# ---------------------------------------------------------------------------


class TestInitializeIdempotency:
    """initialize_provider must call satellites exactly once across multiple calls."""

    def setup_method(self) -> None:
        reset_for_testing()

    def teardown_method(self) -> None:
        reset_for_testing()

    def test_second_call_is_noop(self) -> None:
        """The second call to initialize_provider returns without re-running satellites."""
        cls = _make_concrete("idempotent_test")
        plugin = cls()

        call_count: list[int] = [0]
        original_auth = plugin.register_auth_strategies

        def _counting_auth(logger=None):
            call_count[0] += 1
            return original_auth(logger)

        plugin.register_auth_strategies = _counting_auth  # type: ignore[method-assign]

        plugin.initialize_provider()
        plugin.initialize_provider()  # second call

        assert call_count[0] == 1, "satellites must only run once"

    def test_first_call_marks_initialized(self) -> None:
        """After a successful initialize_provider the guard set contains the name."""
        from orb.providers.base.provider_plugin import _initialized_providers

        cls = _make_concrete("guard_test")
        plugin = cls()
        assert "guard_test" not in _initialized_providers

        plugin.initialize_provider()

        assert "guard_test" in _initialized_providers


# ---------------------------------------------------------------------------
# Failure clears the guard
# ---------------------------------------------------------------------------


class TestFailureClearsGuard:
    """A failed initialize_provider must NOT add the name to the guard set."""

    def setup_method(self) -> None:
        reset_for_testing()

    def teardown_method(self) -> None:
        reset_for_testing()

    def test_failed_init_does_not_poison_guard(self) -> None:
        """If initialize_provider raises, a subsequent call must retry fully."""
        from orb.providers.base.provider_plugin import _initialized_providers

        cls = _make_concrete("failing_provider", fail_on_init=True)
        plugin = cls()

        with pytest.raises(RuntimeError, match="deliberate auth-strategy failure"):
            plugin.initialize_provider()

        # Guard set must NOT contain the provider after failure
        assert "failing_provider" not in _initialized_providers

    def test_retry_after_fix_succeeds(self) -> None:
        """After a failed init, a fixed plugin can initialize on the next call."""
        from orb.providers.base.provider_plugin import _initialized_providers

        cls = _make_concrete("retried_provider", fail_on_init=True)
        plugin = cls()

        # First attempt fails
        with pytest.raises(RuntimeError):
            plugin.initialize_provider()

        assert "retried_provider" not in _initialized_providers

        # "Fix" the plugin by swapping in a no-op auth hook
        plugin.register_auth_strategies = lambda logger=None: None  # type: ignore[method-assign]

        # Second attempt must succeed
        plugin.initialize_provider()
        assert "retried_provider" in _initialized_providers


# ---------------------------------------------------------------------------
# register_plugin classmethod
# ---------------------------------------------------------------------------


class TestRegisterPluginClassmethod:
    """register_plugin must wire the registry and update _REGISTERED_PROVIDERS."""

    def setup_method(self) -> None:
        reset_for_testing()

    def teardown_method(self) -> None:
        reset_for_testing()

    def test_register_plugin_calls_register_provider(self) -> None:
        """register_plugin invokes register_provider on the live registry."""
        import orb.providers.registration as reg_mod

        cls = _make_concrete("plugin_reg_test")

        mock_registry = MagicMock()
        original = list(reg_mod._REGISTERED_PROVIDERS)
        try:
            with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
                cls.register_plugin()

            mock_registry.register_provider.assert_called_once()
            call_kwargs = mock_registry.register_provider.call_args.kwargs
            assert call_kwargs["provider_type"] == "plugin_reg_test"
        finally:
            reg_mod._REGISTERED_PROVIDERS[:] = original

    def test_register_plugin_appends_to_registered_providers(self) -> None:
        """register_plugin appends provider_name to orb.providers.registration._REGISTERED_PROVIDERS."""
        import orb.providers.registration as reg_mod

        original = list(reg_mod._REGISTERED_PROVIDERS)
        try:
            cls = _make_concrete("entry_point_test")
            mock_registry = MagicMock()

            with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
                cls.register_plugin()

            assert "entry_point_test" in reg_mod._REGISTERED_PROVIDERS
        finally:
            # Restore the list to avoid polluting other tests
            reg_mod._REGISTERED_PROVIDERS[:] = original

    def test_register_plugin_is_idempotent(self) -> None:
        """Calling register_plugin twice must not double-register the provider."""
        import orb.providers.registration as reg_mod

        original = list(reg_mod._REGISTERED_PROVIDERS)
        try:
            cls = _make_concrete("idempotent_plugin")
            mock_registry = MagicMock()

            with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
                cls.register_plugin()
                cls.register_plugin()

            count = reg_mod._REGISTERED_PROVIDERS.count("idempotent_plugin")
            assert count == 1, "provider_name must appear exactly once"
        finally:
            reg_mod._REGISTERED_PROVIDERS[:] = original

    def test_register_plugin_raises_when_provider_name_empty(self) -> None:
        """register_plugin must raise ValueError when provider_name is the empty string."""

        class _Unnamed(_make_concrete("placeholder")):  # type: ignore[misc]
            provider_name = ""

        with pytest.raises(ValueError, match="provider_name must be set"):
            _Unnamed.register_plugin()
