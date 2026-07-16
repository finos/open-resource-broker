"""Unit tests for entry-point-driven provider discovery.

Verifies that the ``orb.providers`` entry-point group integrates correctly
with :func:`orb.providers.registration.discover_provider_plugins` and that
the list of discovered providers is appended to ``_REGISTERED_PROVIDERS``.
"""

from __future__ import annotations

import importlib.metadata
from unittest.mock import patch

from orb.providers.registration import _REGISTERED_PROVIDERS, discover_provider_plugins


class _FakeEntryPoint:
    def __init__(self, name: str, target) -> None:
        self.name = name
        self._target = target

    def load(self):
        return self._target


def _stub_entry_points(eps):
    def _inner(group=None):
        return list(eps)

    return patch.object(importlib.metadata, "entry_points", _inner)


def _make_plugin_fn(name: str, side_effects: list):
    """Return a zero-arg callable that appends *name* to *side_effects*."""

    def plugin() -> None:
        side_effects.append(name)

    plugin.__name__ = f"register_{name}_plugin"
    return plugin


def test_discover_populates_registered_providers_list() -> None:
    """A plugin that appends to _REGISTERED_PROVIDERS is reflected in the list."""
    original = list(_REGISTERED_PROVIDERS)
    side: list[str] = []

    def _plugin() -> None:
        side.append("testprovider")
        _REGISTERED_PROVIDERS.append("testprovider")

    eps = [_FakeEntryPoint("testprovider", _plugin)]
    try:
        with _stub_entry_points(eps):
            loaded = discover_provider_plugins(entry_point_group="orb.providers")
        assert "testprovider" in loaded
        assert "testprovider" in _REGISTERED_PROVIDERS
    finally:
        # Restore original state
        _REGISTERED_PROVIDERS.clear()
        _REGISTERED_PROVIDERS.extend(original)


def test_discover_multiple_plugins_called_in_order() -> None:
    """Multiple entry-point plugins are called in iteration order."""
    order: list[str] = []
    eps = [
        _FakeEntryPoint("alpha", _make_plugin_fn("alpha", order)),
        _FakeEntryPoint("beta", _make_plugin_fn("beta", order)),
    ]
    with _stub_entry_points(eps):
        loaded = discover_provider_plugins(entry_point_group="orb.providers.test")

    assert loaded == ["alpha", "beta"]
    assert order == ["alpha", "beta"]


def test_discover_broken_plugin_does_not_block_others() -> None:
    """A plugin that raises must not prevent subsequent plugins from loading."""
    calls: list[str] = []

    def _bad() -> None:
        raise RuntimeError("boom")

    def _good() -> None:
        calls.append("good")

    eps = [
        _FakeEntryPoint("bad", _bad),
        _FakeEntryPoint("good", _good),
    ]
    with _stub_entry_points(eps):
        loaded = discover_provider_plugins(entry_point_group="orb.providers.test")

    assert loaded == ["good"]
    assert calls == ["good"]


def test_discover_no_plugins_returns_empty_list() -> None:
    """No entry points → empty loaded list, no error."""
    with _stub_entry_points([]):
        loaded = discover_provider_plugins(entry_point_group="orb.providers.test")
    assert loaded == []


def test_register_all_providers_calls_discover_first(monkeypatch) -> None:
    """register_all_providers calls discover_provider_plugins before iterating the list."""
    call_log: list[str] = []

    def _fake_discover(*args, **kwargs):
        call_log.append("discover")
        return []

    monkeypatch.setattr("orb.providers.registration.discover_provider_plugins", _fake_discover)
    monkeypatch.setattr("orb.providers.registration._REGISTERED_PROVIDERS", [])

    from orb.providers.registration import register_all_providers

    register_all_providers()
    assert call_log[0] == "discover", "discover_provider_plugins must be called first"
