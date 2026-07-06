"""Shared fixtures and stubs for tests/ui/.

Reflex (rx) is NOT installed in the test virtualenv — it requires Node.js
and a full Reflex init to even import cleanly.  All state tests use a
minimal ``rx`` stub that lets source modules be imported without errors,
and we test the pure-Python logic inside the state/helper functions only.

Import rule: never import ``orb.ui.*`` modules at module scope in test files.
Always import them inside test functions (after rx has been patched into
sys.modules).
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# rx stub — installed into sys.modules before any orb.ui imports
# ---------------------------------------------------------------------------


def _make_rx_stub() -> types.ModuleType:
    """Return a minimal ``reflex`` stub that satisfies all imports in orb.ui."""
    rx = types.ModuleType("reflex")

    # --- LocalStorage stub ------------------------------------------------

    class _LocalStorage(str):
        """Behaves like a str default value but carries the storage key."""

        def __new__(cls, default: str = "", *, name: str = "") -> "_LocalStorage":
            return str.__new__(cls, default)

        def __init__(self, default: str = "", *, name: str = "") -> None:
            self.default = default
            self.storage_name = name

    # --- State base class --------------------------------------------------

    class _FakeState:
        """Minimal rx.State stand-in."""

        def __init__(self) -> None:
            # Apply any LocalStorage defaults defined as class attributes
            for attr_name in dir(type(self)):
                val = getattr(type(self), attr_name)
                if isinstance(val, _LocalStorage):
                    object.__setattr__(self, attr_name, val.default)

    # --- Decorator stubs --------------------------------------------------

    def _passthrough(*args: Any, **kwargs: Any) -> Any:
        """Return the decorated function/class unchanged."""
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        # Called with arguments: rx.event(background=True), rx.var, etc.
        def _inner(fn: Any) -> Any:
            return fn

        return _inner

    # --- Populate module with setattr (pyright-friendly) ------------------

    _component = MagicMock(name="rx.component")

    attrs: dict[str, Any] = {
        "__version__": "0.0.0-stub",
        "State": _FakeState,
        "LocalStorage": _LocalStorage,
        "event": _passthrough,
        "var": _passthrough,
        # Component stubs — MagicMock absorbs any call or attribute access
        "Component": _component,
        "box": _component,
        "vstack": _component,
        "hstack": _component,
        "flex": _component,
        "grid": _component,
        "text": _component,
        "heading": _component,
        "badge": _component,
        "button": _component,
        "icon": _component,
        "icon_button": _component,
        "link": _component,
        "spacer": _component,
        "cond": _component,
        "match": _component,
        "foreach": _component,
        "spinner": _component,
        "center": _component,
        "divider": _component,
        "fragment": _component,
        "code": _component,
        "input": _component,
        "checkbox": _component,
        "card": _component,
        "table": MagicMock(name="rx.table"),
        "tooltip": _component,
        "skeleton": _component,
        "callout": MagicMock(name="rx.callout"),
        "alert_dialog": MagicMock(name="rx.alert_dialog"),
        "recharts": MagicMock(name="rx.recharts"),
        "Var": MagicMock(name="rx.Var"),
        "color": MagicMock(name="rx.color"),
        "breakpoints": MagicMock(name="rx.breakpoints"),
    }
    for key, val in attrs.items():
        setattr(rx, key, val)

    return rx


def _install_rx_stub() -> None:
    """Inject the rx stub into sys.modules if reflex is not already present."""
    if "reflex" not in sys.modules:
        stub = _make_rx_stub()
        sys.modules["reflex"] = stub
        sys.modules["rx"] = stub
        # Some Reflex internals import sub-packages — stub them too
        for sub in [
            "reflex.vars",
            "reflex.state",
            "reflex.components",
            "reflex.components.core",
        ]:
            sys.modules.setdefault(sub, types.ModuleType(sub))


# Install at conftest load time so any test file can do a top-level import
# of the orb.ui modules without hitting ModuleNotFoundError.
_install_rx_stub()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_mock_api_client(**overrides: Any) -> MagicMock:
    """Return a MagicMock with async-compatible replacements for every api function.

    All async functions return sensible empty payloads by default.
    Pass keyword arguments to override specific methods.

    Usage::

        mock_api = make_mock_api_client(
            list_machines=AsyncMock(return_value={"machines": [m1, m2]})
        )
    """
    client = MagicMock()
    defaults: dict[str, Any] = {
        "get_health": AsyncMock(return_value={"status": "ok"}),
        "get_info": AsyncMock(return_value={"version": "1.0.0"}),
        "get_me": AsyncMock(
            return_value={
                "username": "testuser",
                "role": "operator",
                "permissions": ["request_machines", "return_machines"],
            }
        ),
        "list_machines": AsyncMock(return_value={"machines": []}),
        "get_machine": AsyncMock(return_value={}),
        "list_requests": AsyncMock(return_value={"requests": []}),
        "list_templates": AsyncMock(return_value={"templates": []}),
        "get_dashboard_summary": AsyncMock(
            return_value={
                "machines": {"total": 0, "by_status": {}},
                "requests": {"total": 0, "in_flight": 0, "by_status": {}},
                "templates": {"total": 0, "by_provider_api": {}},
                "recent_activity": [],
            }
        ),
        "wipe_database": AsyncMock(return_value={"rows_deleted": 0, "tables_truncated": []}),
    }
    defaults.update(overrides)
    for attr, val in defaults.items():
        setattr(client, attr, val)
    return client
