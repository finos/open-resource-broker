"""Unit tests for orb.providers.registration — uncovered branches."""

from __future__ import annotations

import importlib.metadata
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
