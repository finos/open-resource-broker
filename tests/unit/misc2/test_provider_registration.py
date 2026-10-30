"""Unit tests for orb.providers.registration — uncovered branches."""

from __future__ import annotations

import importlib.metadata
import logging
from unittest.mock import MagicMock, patch

import pytest

from orb.providers import registration as reg_module
from orb.providers.registration import (
    _REGISTERED_PROVIDERS,
    discover_provider_plugins,
    register_all_defaults_loaders,
    register_all_provider_cli_specs,
    register_all_provider_types,
    register_all_providers,
    register_fallback_provider,
)

# ---------------------------------------------------------------------------
# Helper: fake entry point
# ---------------------------------------------------------------------------


class _FakeEP:
    def __init__(self, name: str, target=None, *, load_raises=None, not_callable=False):
        self.name = name
        self._target = target
        self._load_raises = load_raises
        self._not_callable = not_callable

    def load(self):
        if self._load_raises:
            raise self._load_raises
        if self._not_callable:
            return "not_a_callable"
        return self._target


def _stub_eps(eps):
    return patch.object(importlib.metadata, "entry_points", lambda group=None: list(eps))


# ---------------------------------------------------------------------------
# discover_provider_plugins
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoverProviderPlugins:
    def test_empty_entry_points_returns_empty_list(self) -> None:
        with _stub_eps([]):
            result = discover_provider_plugins()
        assert result == []

    def test_successful_plugin_is_returned(self) -> None:
        called = []
        ep = _FakeEP("myprovider", target=lambda: called.append("myprovider"))
        with _stub_eps([ep]):
            result = discover_provider_plugins()
        assert "myprovider" in result
        assert "myprovider" in called

    def test_plugin_load_error_is_skipped(self) -> None:
        ep = _FakeEP("bad_ep", load_raises=ImportError("missing dep"))
        with _stub_eps([ep]):
            result = discover_provider_plugins()
        assert "bad_ep" not in result

    def test_non_callable_plugin_is_skipped(self) -> None:
        ep = _FakeEP("non_callable_ep", not_callable=True)
        with _stub_eps([ep]):
            result = discover_provider_plugins()
        assert "non_callable_ep" not in result

    def test_plugin_callable_raises_is_skipped(self) -> None:
        def _bad_plugin():
            raise RuntimeError("plugin exploded")

        ep = _FakeEP("exploding_ep", target=_bad_plugin)
        with _stub_eps([ep]):
            result = discover_provider_plugins()
        assert "exploding_ep" not in result

    def test_multiple_plugins_only_successful_returned(self) -> None:
        called = []
        ep_ok = _FakeEP("good", target=lambda: called.append("good"))
        ep_fail = _FakeEP("fail", load_raises=Exception("fail"))
        with _stub_eps([ep_ok, ep_fail]):
            result = discover_provider_plugins()
        assert "good" in result
        assert "fail" not in result


# ---------------------------------------------------------------------------
# register_all_providers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterAllProviders:
    def test_calls_discover_plugins_and_skips_missing_module(self) -> None:
        """register_all_providers handles providers with no module gracefully."""
        original = list(_REGISTERED_PROVIDERS)
        _REGISTERED_PROVIDERS.clear()
        _REGISTERED_PROVIDERS.append("__nonexistent_provider__")
        try:
            with _stub_eps([]):
                with patch("orb.providers.registry.get_provider_registry") as mock_reg:
                    mock_reg.return_value = MagicMock()
                    register_all_providers()
        finally:
            _REGISTERED_PROVIDERS.clear()
            _REGISTERED_PROVIDERS.extend(original)

    def test_register_all_providers_deprecated_alias(self) -> None:
        """register_all_provider_types is an alias for register_all_providers."""
        with patch.object(reg_module, "register_all_providers") as mock_fn:
            register_all_provider_types()
            mock_fn.assert_called_once_with(container=None)


def _raises():
    raise RuntimeError("plugin exploded")


_BAD_PLUGINS = {
    "load_import_error": lambda name: _FakeEP(name, load_raises=ImportError("missing dep")),
    "load_error": lambda name: _FakeEP(name, load_raises=ValueError("bad target")),
    "non_callable": lambda name: _FakeEP(name, not_callable=True),
    "callable_raises": lambda name: _FakeEP(name, target=_raises),
}


@pytest.mark.unit
class TestPluginFailureEscalation:
    """A failing plugin fails startup only for a configured provider, and only in
    ``register_all_providers``."""

    @staticmethod
    def _run_register(eps, configured, registered=()):
        original = list(_REGISTERED_PROVIDERS)
        _REGISTERED_PROVIDERS.clear()
        _REGISTERED_PROVIDERS.extend(registered)
        try:
            with (
                _stub_eps(eps),
                patch.object(
                    reg_module,
                    "_configured_provider_types",
                    return_value=frozenset(configured),
                ),
                patch("orb.providers.registry.get_provider_registry", return_value=MagicMock()),
            ):
                register_all_providers()
        finally:
            _REGISTERED_PROVIDERS.clear()
            _REGISTERED_PROVIDERS.extend(original)

    def test_discovery_records_failures_for_the_caller(self) -> None:
        failures: dict[str, Exception] = {}
        eps = [_BAD_PLUGINS[kind](kind) for kind in _BAD_PLUGINS]
        with _stub_eps(eps):
            assert discover_provider_plugins(failures=failures) == []
        assert set(failures) == set(_BAD_PLUGINS)
        assert isinstance(failures["load_import_error"], ImportError)
        assert isinstance(failures["non_callable"], TypeError)

    @pytest.mark.parametrize("kind", list(_BAD_PLUGINS))
    def test_configured_plugin_failure_fails_startup(self, kind: str) -> None:
        with pytest.raises(RuntimeError, match="Configured provider 'myprov' could not be loaded"):
            self._run_register([_BAD_PLUGINS[kind]("myprov")], configured={"myprov"})

    def test_missing_extra_hint_is_included(self) -> None:
        with pytest.raises(RuntimeError, match=r"pip install 'orb-py\[myprov\]'"):
            self._run_register([_BAD_PLUGINS["load_import_error"]("myprov")], configured={"myprov"})

    @pytest.mark.parametrize("kind", list(_BAD_PLUGINS))
    def test_unconfigured_plugin_failure_is_logged_and_good_plugin_loads(
        self, kind: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        called: list[str] = []
        eps = [
            _BAD_PLUGINS[kind]("badprov"),
            _FakeEP("goodprov", target=lambda: called.append("goodprov")),
        ]
        with caplog.at_level("WARNING", logger=reg_module.__name__):
            self._run_register(eps, configured={"other"})
        assert called == ["goodprov"]
        assert any("badprov" in record.getMessage() for record in caplog.records)

    @pytest.mark.parametrize("kind", list(_BAD_PLUGINS))
    def test_bad_plugin_does_not_block_a_good_configured_one(self, kind: str) -> None:
        called: list[str] = []
        eps = [
            _BAD_PLUGINS[kind]("badprov"),
            _FakeEP("goodprov", target=lambda: called.append("goodprov")),
        ]
        self._run_register(eps, configured={"goodprov"})
        assert called == ["goodprov"]

    def test_unknown_configuration_treats_plugin_as_optional(self) -> None:
        original = list(_REGISTERED_PROVIDERS)
        try:
            with (
                _stub_eps([_BAD_PLUGINS["callable_raises"]("myprov")]),
                patch.object(reg_module, "_configured_provider_types", return_value=None),
                patch("orb.providers.registry.get_provider_registry", return_value=MagicMock()),
            ):
                register_all_providers()
        finally:
            _REGISTERED_PROVIDERS[:] = original

    def test_in_tree_provider_with_failing_entry_point_fails_once(self) -> None:
        with (
            patch.object(
                reg_module, "_provider_load_failure", wraps=reg_module._provider_load_failure
            ) as spy,
            patch.object(reg_module.importlib, "import_module") as import_module,
            pytest.raises(RuntimeError, match="Configured provider 'alpha'"),
        ):
            self._run_register(
                [_BAD_PLUGINS["load_import_error"]("alpha")],
                configured={"alpha"},
                registered=["alpha"],
            )
        assert spy.call_count == 1
        assert all(
            c.args != ("orb.providers.alpha.registration",) for c in import_module.call_args_list
        )

    def test_unconfigured_in_tree_provider_with_failing_entry_point_is_reported_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            patch.object(
                reg_module, "_provider_load_failure", wraps=reg_module._provider_load_failure
            ) as spy,
            caplog.at_level("WARNING", logger=reg_module.__name__),
        ):
            self._run_register(
                [_BAD_PLUGINS["load_import_error"]("alpha")],
                configured=set(),
                registered=["alpha"],
            )
        assert spy.call_count == 1

    @pytest.mark.parametrize("kind", list(_BAD_PLUGINS))
    def test_unconfigured_broken_plugin_is_logged_once_across_all_bootstrap_paths(
        self, kind: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            _stub_eps([_BAD_PLUGINS[kind]("brokenprov")]),
            patch.object(reg_module, "_configured_provider_types", return_value=frozenset()),
            patch("orb.providers.registry.get_provider_registry", return_value=MagicMock()),
            patch.object(reg_module, "_REGISTERED_PROVIDERS", []),
            caplog.at_level("DEBUG", logger=reg_module.__name__),
        ):
            register_all_provider_cli_specs()
            register_all_defaults_loaders()
            register_all_providers()
            register_all_providers()
        reports = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "brokenprov" in r.getMessage()
        ]
        assert len(reports) == 1

    def test_configured_broken_plugin_still_fails_after_it_was_logged(self) -> None:
        with (
            _stub_eps([_BAD_PLUGINS["load_error"]("myprov")]),
            patch.object(
                reg_module, "_configured_provider_types", return_value=frozenset({"myprov"})
            ),
            patch("orb.providers.registry.get_provider_registry", return_value=MagicMock()),
            patch.object(reg_module, "_REGISTERED_PROVIDERS", []),
        ):
            register_all_provider_cli_specs()
            with pytest.raises(RuntimeError, match="Configured provider 'myprov'"):
                register_all_providers()

    def test_failing_entry_point_named_like_a_builtin_does_not_suppress_it(self) -> None:
        fake_module = MagicMock()
        registry = MagicMock()
        with (
            _stub_eps([_BAD_PLUGINS["load_import_error"]("aws")]),
            patch.object(reg_module, "_configured_provider_types", return_value=frozenset()),
            patch("orb.providers.registry.get_provider_registry", return_value=registry),
            patch.object(reg_module, "_REGISTERED_PROVIDERS", ["aws"]),
            patch.object(reg_module.importlib, "import_module", return_value=fake_module),
        ):
            register_all_providers()
        fake_module.register_aws_provider.assert_called_once_with(registry)

    @pytest.mark.parametrize("kind", list(_BAD_PLUGINS))
    def test_cli_spec_and_defaults_paths_never_raise_for_a_configured_plugin(
        self, kind: str
    ) -> None:
        with (
            _stub_eps([_BAD_PLUGINS[kind]("myprov")]),
            patch.object(
                reg_module, "_configured_provider_types", return_value=frozenset({"myprov"})
            ),
            patch.object(reg_module, "_REGISTERED_PROVIDERS", []),
        ):
            register_all_provider_cli_specs()
            register_all_defaults_loaders()


# ---------------------------------------------------------------------------
# register_all_provider_cli_specs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterAllProviderCliSpecs:
    def test_handles_empty_registered_providers_list(self) -> None:
        """Does not raise when _REGISTERED_PROVIDERS is empty."""
        original = list(_REGISTERED_PROVIDERS)
        _REGISTERED_PROVIDERS.clear()
        try:
            with _stub_eps([]):
                register_all_provider_cli_specs()
        finally:
            _REGISTERED_PROVIDERS.clear()
            _REGISTERED_PROVIDERS.extend(original)

    def test_skips_provider_with_no_cli_spec_module(self) -> None:
        """Providers without cli spec modules are silently skipped."""
        original = list(_REGISTERED_PROVIDERS)
        _REGISTERED_PROVIDERS.clear()
        _REGISTERED_PROVIDERS.append("__no_cli_spec_provider__")
        try:
            with _stub_eps([]):
                with patch("importlib.util.find_spec", return_value=None):
                    register_all_provider_cli_specs()
        finally:
            _REGISTERED_PROVIDERS.clear()
            _REGISTERED_PROVIDERS.extend(original)


# ---------------------------------------------------------------------------
# register_all_defaults_loaders
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterAllDefaultsLoaders:
    def test_handles_empty_registered_providers_list(self) -> None:
        original = list(_REGISTERED_PROVIDERS)
        _REGISTERED_PROVIDERS.clear()
        try:
            with _stub_eps([]):
                register_all_defaults_loaders()
        finally:
            _REGISTERED_PROVIDERS.clear()
            _REGISTERED_PROVIDERS.extend(original)

    def test_skips_provider_with_no_defaults_loader_module(self) -> None:
        original = list(_REGISTERED_PROVIDERS)
        _REGISTERED_PROVIDERS.clear()
        _REGISTERED_PROVIDERS.append("__no_defaults_loader_provider__")
        try:
            with _stub_eps([]):
                with patch("importlib.util.find_spec", return_value=None):
                    register_all_defaults_loaders()
        finally:
            _REGISTERED_PROVIDERS.clear()
            _REGISTERED_PROVIDERS.extend(original)


# ---------------------------------------------------------------------------
# register_fallback_provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterFallbackProvider:
    def test_registers_fallback_strategy(self) -> None:
        """register_fallback_provider creates and registers a FallbackProviderStrategy."""
        primary = MagicMock()
        fallbacks = [MagicMock()]
        mock_registry = MagicMock()
        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            with patch(
                "orb.providers.base.strategy.fallback_strategy.FallbackProviderStrategy"
            ) as MockFS:
                mock_strategy = MagicMock()
                MockFS.return_value = mock_strategy
                register_fallback_provider(
                    primary_strategy=primary,
                    fallback_strategies=fallbacks,
                )
                MockFS.assert_called_once()
                mock_registry.register_fallback_strategy.assert_called_once_with(mock_strategy)

    def test_uses_default_logger_when_none_provided(self) -> None:
        primary = MagicMock()
        mock_registry = MagicMock()
        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            with patch(
                "orb.providers.base.strategy.fallback_strategy.FallbackProviderStrategy"
            ) as MockFS:
                MockFS.return_value = MagicMock()
                register_fallback_provider(primary_strategy=primary, fallback_strategies=[])
                call_kwargs = MockFS.call_args[1]
                assert call_kwargs.get("logger") is not None
