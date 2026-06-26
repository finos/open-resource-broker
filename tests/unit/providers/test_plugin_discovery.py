"""Unit tests for the ``orb.providers`` plugin discovery mechanism.

Covers :func:`orb.providers.registration.discover_provider_plugins` -
the entry-point-driven extension point documented at
``docs/root/providers/kubernetes/plugin-authoring.md``.

Tests use a simulated entry-point group so we can exercise success and
failure paths without installing a real distribution.
"""

from __future__ import annotations

import importlib.metadata
from typing import Any
from unittest.mock import patch

import pytest

from orb.providers.registration import discover_provider_plugins


class _FakeEntryPoint:
    """Minimal stand-in for :class:`importlib.metadata.EntryPoint`.

    Implements just enough of the surface (``name``, ``load()``) for the
    discovery code to consume it.
    """

    def __init__(self, name: str, target: Any) -> None:
        self.name = name
        self._target = target

    def load(self) -> Any:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


def _patched_entry_points(eps: list[_FakeEntryPoint]):
    """Patch ``importlib.metadata.entry_points`` to return ``eps``.

    The patched function ignores the ``group=`` kwarg - the test driver
    is responsible for only passing entry points that belong to the
    group under test.
    """

    def _stub(group: str | None = None):
        return list(eps)

    return patch.object(importlib.metadata, "entry_points", _stub)


def test_discover_loads_callable_entry_point() -> None:
    """A well-formed entry point is loaded and invoked exactly once."""
    calls: list[str] = []

    def register() -> None:
        calls.append("registered")

    eps = [_FakeEntryPoint("fake_plugin", register)]
    with _patched_entry_points(eps):
        loaded = discover_provider_plugins(entry_point_group="orb.providers.test")

    assert loaded == ["fake_plugin"]
    assert calls == ["registered"]


def test_discover_skips_non_callable_target() -> None:
    """Entry points whose target is not callable are skipped, not raised."""
    eps = [_FakeEntryPoint("bad_plugin", "not-a-callable")]
    with _patched_entry_points(eps):
        loaded = discover_provider_plugins(entry_point_group="orb.providers.test")
    assert loaded == []


def test_discover_swallows_load_errors() -> None:
    """``EntryPoint.load`` raising must not propagate out of discovery."""
    eps = [_FakeEntryPoint("broken_load", ImportError("missing dep"))]
    with _patched_entry_points(eps):
        loaded = discover_provider_plugins(entry_point_group="orb.providers.test")
    assert loaded == []


def test_discover_swallows_register_errors_and_continues() -> None:
    """A plugin raising during register() must not block the next plugin."""
    calls: list[str] = []

    def good() -> None:
        calls.append("good")

    def bad() -> None:
        raise RuntimeError("plugin exploded")

    eps = [
        _FakeEntryPoint("bad_plugin", bad),
        _FakeEntryPoint("good_plugin", good),
    ]
    with _patched_entry_points(eps):
        loaded = discover_provider_plugins(entry_point_group="orb.providers.test")

    assert loaded == ["good_plugin"]
    assert calls == ["good"]


def test_discover_returns_empty_when_no_plugins_present() -> None:
    """No entry points in the group means no plugins loaded, no error raised."""
    with _patched_entry_points([]):
        loaded = discover_provider_plugins(entry_point_group="orb.providers.test")
    assert loaded == []


def test_discover_default_group_is_orb_providers() -> None:
    """The default group is the documented ``orb.providers`` value."""
    calls: list[str] = []

    captured_group: dict[str, str] = {}

    def _stub(group: str | None = None):
        captured_group["group"] = group or ""
        return []

    with patch.object(importlib.metadata, "entry_points", _stub):
        discover_provider_plugins()

    assert captured_group["group"] == "orb.providers"
    # Sanity - the stub did not invoke any plugin.
    assert calls == []


def test_discover_invokes_plugins_in_iteration_order() -> None:
    """Plugins are loaded in entry-point iteration order."""
    order: list[str] = []

    def make(name: str):
        def fn() -> None:
            order.append(name)

        return fn

    eps = [
        _FakeEntryPoint("first", make("first")),
        _FakeEntryPoint("second", make("second")),
        _FakeEntryPoint("third", make("third")),
    ]
    with _patched_entry_points(eps):
        loaded = discover_provider_plugins(entry_point_group="orb.providers.test")

    assert loaded == ["first", "second", "third"]
    assert order == ["first", "second", "third"]


@pytest.mark.parametrize("group_name", ["orb.providers", "orb.providers.custom"])
def test_discover_passes_custom_group(group_name: str) -> None:
    """Caller-supplied group is forwarded to ``entry_points``."""
    captured: dict[str, str] = {}

    def _stub(group: str | None = None):
        captured["group"] = group or ""
        return []

    with patch.object(importlib.metadata, "entry_points", _stub):
        discover_provider_plugins(entry_point_group=group_name)

    assert captured["group"] == group_name
