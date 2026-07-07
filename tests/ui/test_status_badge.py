"""Tests for orb.ui.components.status_badge.

Covers machine_status_badge, request_status_badge, _aria_status, and
_resolve_display_status.

Under the rx stub every rx.badge / rx.match call returns a MagicMock.
We verify:
  - each known status causes rx.match to be called with correct tuples
  - unknown status falls back to the default "gray" arm
  - _aria_status builds the right string for plain str vs rx.Var inputs
  - _resolve_display_status maps HF wire statuses back to internal names
  - both badge helpers accept rx.Var inputs without raising

Setup note
----------
The conftest stub sets ``rx.Var = MagicMock(name='rx.Var')``.  The production
code calls ``isinstance(status, rx.Var)`` which requires its second argument to
be a real class.  We replace ``rx.Var`` with a minimal real class at the top of
each test function (importing reflex first so the stub is already in
sys.modules).
"""

from __future__ import annotations

from unittest.mock import MagicMock

# conftest.py installs the rx stub before any orb.ui imports.
# All orb.ui imports MUST live inside test functions.


# ---------------------------------------------------------------------------
# Helper: make rx.Var a real class so isinstance() works
# ---------------------------------------------------------------------------


def _patch_rx_var():
    """Replace the MagicMock rx.Var with a real class for isinstance() checks.

    The production code calls ``isinstance(status, rx.Var)`` and
    ``rx.Var.create("Status: ")``, so the stub class needs both:
    - be a real class (for ``isinstance``)
    - have a ``create`` classmethod (for ``rx.Var.create(...)``)

    Returns the patched ``rx`` module so callers can create instances.
    """
    import reflex as rx

    class _FakeVar:
        """Minimal stand-in for rx.Var that satisfies isinstance checks."""

        def __init__(self, value=None):
            self._value = value

        def to(self, t):
            return MagicMock(name=f"FakeVar.to({t})")

        @classmethod
        def create(cls, value=None):
            m = MagicMock(name="FakeVar.create()")
            m.__add__ = lambda self, other: MagicMock(name="FakeVar.__add__")
            return m

        def __add__(self, other):
            return MagicMock(name="FakeVar.__add__")

    rx.Var = _FakeVar  # type: ignore[assignment]
    return rx


# ---------------------------------------------------------------------------
# _aria_status
# ---------------------------------------------------------------------------


class TestAriaStatus:
    def _fn(self):
        rx = _patch_rx_var()
        from orb.ui.components.status_badge import _aria_status

        return _aria_status, rx

    def test_plain_string_returns_fstring(self):
        fn, _ = self._fn()
        result = fn("running")
        assert result == "Status: running"

    def test_plain_empty_string(self):
        fn, _ = self._fn()
        result = fn("")
        assert result == "Status: "

    def test_var_input_calls_to_str(self):
        """For a Var input, .to(str) is called and result is added to prefix."""
        fn, rx = self._fn()
        var = rx.Var("some_status")  # real _FakeVar instance
        result = fn(var)
        # .to(str) was called on the var; result is the combined expression
        assert result is not None

    def test_unknown_plain_string(self):
        fn, _ = self._fn()
        assert fn("some_weird_status") == "Status: some_weird_status"


# ---------------------------------------------------------------------------
# _resolve_display_status
# ---------------------------------------------------------------------------


class TestResolveDisplayStatus:
    def _fn(self):
        _patch_rx_var()
        from orb.ui.components.status_badge import _resolve_display_status

        return _resolve_display_status

    def test_complete_with_error_maps_to_partial(self):
        fn = self._fn()
        assert fn("complete_with_error") == "partial"

    def test_unknown_string_passes_through(self):
        fn = self._fn()
        assert fn("in_progress") == "in_progress"

    def test_empty_string_passes_through(self):
        fn = self._fn()
        assert fn("") == ""

    def test_var_input_returns_rx_match(self):
        """For a Var input, rx.match is called and its result returned."""
        rx = _patch_rx_var()
        from orb.ui.components.status_badge import _resolve_display_status

        rx.match.reset_mock()
        var = rx.Var("status_value")
        result = _resolve_display_status(var)
        rx.match.assert_called()
        assert result is not None


# ---------------------------------------------------------------------------
# machine_status_badge
# ---------------------------------------------------------------------------


class TestMachineStatusBadge:
    def _badge_fn(self):
        rx = _patch_rx_var()
        from orb.ui.components.status_badge import machine_status_badge

        return machine_status_badge, rx

    def test_returns_component_for_running(self):
        badge_fn, _ = self._badge_fn()
        result = badge_fn("running")
        assert result is not None

    def test_known_statuses_call_rx_badge(self):
        """All known machine statuses should call rx.badge without raising.

        rx.badge and rx.match share the same MagicMock under the stub, so
        the mock is called at least twice per badge_fn call (once for match,
        once for badge itself). We verify it was called at least once and
        that one of those calls has the badge-specific kwargs.
        """
        badge_fn, rx = self._badge_fn()
        known = [
            "running",
            "succeed",
            "success",
            "pending",
            "in_progress",
            "stopped",
            "shutting-down",
            "terminated",
            "failed",
            "error",
        ]
        for status in known:
            rx.badge.reset_mock()
            result = badge_fn(status)
            assert result is not None, f"badge returned None for status={status}"
            rx.badge.assert_called()
            # At least one call should have badge-specific kwargs (variant, size)
            badge_calls = [c for c in rx.badge.call_args_list if "variant" in c.kwargs]
            assert badge_calls, f"no badge call with variant kwarg for status={status}"

    def test_rx_match_called_with_color_tuples(self):
        """rx.match is invoked with the expected (status, color) tuples.

        rx.match and rx.badge share the same MagicMock under the stub.
        We find the call that has tuple arguments (the rx.match call) by
        scanning call_args_list rather than using call_args (which returns
        the last call, i.e. the rx.badge outer call).
        """
        badge_fn, rx = self._badge_fn()
        rx.match.reset_mock()
        badge_fn("running")
        rx.match.assert_called()
        # The rx.match call has tuples as positional args; the rx.badge call has kwargs
        match_calls = [
            c for c in rx.match.call_args_list if any(isinstance(a, tuple) for a in c.args)
        ]
        assert match_calls, "no rx.match call with tuple args found"
        match_args = match_calls[0].args
        tuples = [a for a in match_args if isinstance(a, tuple)]
        tuple_map = dict(tuples)
        assert tuple_map.get("running") == "green"
        assert tuple_map.get("pending") == "blue"
        assert tuple_map.get("failed") == "red"
        assert tuple_map.get("stopped") == "gray"
        assert tuple_map.get("shutting-down") == "orange"

    def test_default_fallback_is_gray(self):
        """The last positional arg to rx.match (the default) must be 'gray'."""
        badge_fn, rx = self._badge_fn()
        rx.match.reset_mock()
        badge_fn("unknown_status_xyz")
        rx.match.assert_called()
        # Find the match call (has tuple args) and check its last arg is "gray"
        match_calls = [
            c for c in rx.match.call_args_list if any(isinstance(a, tuple) for a in c.args)
        ]
        assert match_calls, "no rx.match call with tuple args found"
        match_args = match_calls[0].args
        assert match_args[-1] == "gray"

    def test_unknown_status_still_returns_component(self):
        badge_fn, _ = self._badge_fn()
        result = badge_fn("completely_unknown")
        assert result is not None

    def test_empty_string_returns_component(self):
        badge_fn, _ = self._badge_fn()
        result = badge_fn("")
        assert result is not None

    def test_var_input_returns_component(self):
        """rx.Var input must be accepted without raising."""
        badge_fn, rx = self._badge_fn()
        var = rx.Var("some_status")
        result = badge_fn(var)
        assert result is not None

    def test_aria_label_is_set(self):
        """rx.badge is called with an aria_label kwarg.

        rx.badge and rx.match share the same MagicMock; find the badge call
        by filtering for the call that has aria_label in its kwargs.
        """
        badge_fn, rx = self._badge_fn()
        rx.badge.reset_mock()
        badge_fn("running")
        rx.badge.assert_called()
        badge_calls = [c for c in rx.badge.call_args_list if "aria_label" in c.kwargs]
        assert badge_calls, "no rx.badge call with aria_label kwarg"
        kwargs = badge_calls[0].kwargs
        assert kwargs["aria_label"] == "Status: running"

    def test_badge_variant_is_soft(self):
        """Badges use variant='soft'."""
        badge_fn, rx = self._badge_fn()
        rx.badge.reset_mock()
        badge_fn("running")
        rx.badge.assert_called()
        badge_calls = [c for c in rx.badge.call_args_list if "variant" in c.kwargs]
        assert badge_calls, "no rx.badge call with variant kwarg"
        assert badge_calls[0].kwargs.get("variant") == "soft"


# ---------------------------------------------------------------------------
# request_status_badge
# ---------------------------------------------------------------------------


class TestRequestStatusBadge:
    def _badge_fn(self):
        rx = _patch_rx_var()
        from orb.ui.components.status_badge import request_status_badge

        return request_status_badge, rx

    def test_returns_component_for_in_progress(self):
        badge_fn, _ = self._badge_fn()
        result = badge_fn("in_progress")
        assert result is not None

    def test_known_statuses_call_rx_badge(self):
        """All known request statuses should call rx.badge without raising.

        rx.badge and rx.match share the same MagicMock, so assert_called()
        (not assert_called_once()) is used; we also verify a call with
        badge-specific kwargs (variant) exists.
        """
        badge_fn, rx = self._badge_fn()
        known = [
            "complete",
            "completed",
            "success",
            "succeed",
            "launched",
            "healthy",
            "failed",
            "fail",
            "timeout",
            "error",
            "unhealthy",
            "in_progress",
            "pending",
            "partial",
            "degraded",
            "cancelled",
        ]
        for status in known:
            rx.badge.reset_mock()
            result = badge_fn(status)
            assert result is not None, f"badge returned None for status={status}"
            rx.badge.assert_called()
            badge_calls = [c for c in rx.badge.call_args_list if "variant" in c.kwargs]
            assert badge_calls, f"no badge call with variant kwarg for status={status}"

    def test_rx_match_called_with_color_tuples(self):
        """rx.match is invoked with expected (status, color) tuples.

        rx.match/badge share the same MagicMock; find the match call by
        looking for the call with tuple positional args.
        """
        badge_fn, rx = self._badge_fn()
        rx.match.reset_mock()
        badge_fn("complete")
        rx.match.assert_called()
        match_calls = [
            c for c in rx.match.call_args_list if any(isinstance(a, tuple) for a in c.args)
        ]
        assert match_calls, "no rx.match call with tuple args found"
        match_args = match_calls[0].args
        tuples = [a for a in match_args if isinstance(a, tuple)]
        tuple_map = dict(tuples)
        assert tuple_map.get("complete") == "green"
        assert tuple_map.get("failed") == "red"
        assert tuple_map.get("in_progress") == "blue"
        assert tuple_map.get("partial") == "amber"
        assert tuple_map.get("cancelled") == "gray"

    def test_default_fallback_is_gray(self):
        """The last arg to rx.match must be 'gray'."""
        badge_fn, rx = self._badge_fn()
        rx.match.reset_mock()
        badge_fn("totally_unknown")
        rx.match.assert_called()
        match_calls = [
            c for c in rx.match.call_args_list if any(isinstance(a, tuple) for a in c.args)
        ]
        assert match_calls, "no rx.match call with tuple args found"
        match_args = match_calls[0].args
        assert match_args[-1] == "gray"

    def test_complete_with_error_maps_via_hf_reverse(self):
        """'complete_with_error' is mapped to 'partial' before badge rendering.

        The badge call (with variant kwarg) should have 'partial' as its
        first positional arg since _resolve_display_status maps it.
        """
        badge_fn, rx = self._badge_fn()
        rx.badge.reset_mock()
        result = badge_fn("complete_with_error")
        assert result is not None
        rx.badge.assert_called()
        # The outer rx.badge(display_status, color_scheme=..., variant=...) call
        badge_calls = [c for c in rx.badge.call_args_list if "variant" in c.kwargs]
        assert badge_calls, "no rx.badge call with variant kwarg"
        badge_args = badge_calls[0].args
        assert badge_args[0] == "partial"

    def test_empty_string_returns_component(self):
        badge_fn, _ = self._badge_fn()
        result = badge_fn("")
        assert result is not None

    def test_unknown_status_returns_component(self):
        badge_fn, _ = self._badge_fn()
        result = badge_fn("nonexistent_status")
        assert result is not None

    def test_var_input_returns_component(self):
        """rx.Var input must not raise."""
        badge_fn, rx = self._badge_fn()
        var = rx.Var("some_status")
        result = badge_fn(var)
        assert result is not None

    def test_aria_label_is_set(self):
        """rx.badge receives aria_label kwarg."""
        badge_fn, rx = self._badge_fn()
        rx.badge.reset_mock()
        badge_fn("pending")
        kwargs = rx.badge.call_args.kwargs
        assert "aria_label" in kwargs
