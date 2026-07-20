"""Coverage-gap tests for orb.ui.state and orb.ui.components.provider_columns.

Extends (does NOT duplicate) test_state.py, test_state_integration.py,
test_provider_columns.py, and test_cell_formatters.py.  Targets the
remaining uncovered event-handler branches in AppState/CurrentUserState and
the formatter closures + skip branches in provider_columns.

The rx stub from conftest.py satisfies all imports; no Reflex runtime is
required.  All orb.ui imports live inside test functions (import rule).
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Harness — no-op async context manager so ``async with self:`` works.
# ---------------------------------------------------------------------------


def _make_testable_subclass(StateClass):
    """Return a subclass with a no-op async context manager."""

    class _TestableState(StateClass):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    _TestableState.__name__ = f"Testable{StateClass.__name__}"
    _TestableState.__qualname__ = _TestableState.__name__
    return _TestableState


# ---------------------------------------------------------------------------
# AppState.load_provider_schemas — single-flight guard + success + failure
# ---------------------------------------------------------------------------


class TestLoadProviderSchemas:
    """AppState.load_provider_schemas background event handler."""

    def _make_state(self):
        from orb.ui.state import AppState

        TestableState = _make_testable_subclass(AppState)
        s = TestableState.__new__(TestableState)
        s.provider_schemas = {}
        s._schemas_loaded = False
        return s

    @pytest.mark.asyncio
    async def test_populates_schemas_on_success(self):
        """A successful fetch stores the dict and marks schemas loaded."""
        s = self._make_state()
        payload = {
            "aws": [
                {
                    "key": "aws_instance_type",
                    "path": "provider_data.instance_type",
                    "label": "Instance Type",
                    "kind": "text",
                    "resource_type": "machines",
                }
            ]
        }
        with patch("orb.ui.state.api") as mock_api:
            mock_api.get_provider_schemas = AsyncMock(return_value=payload)
            await s.load_provider_schemas()

        assert s.provider_schemas == payload
        assert s._schemas_loaded is True

    @pytest.mark.asyncio
    async def test_single_flight_guard_skips_when_already_loaded(self):
        """When _schemas_loaded is already True the API is never called."""
        s = self._make_state()
        s._schemas_loaded = True

        with patch("orb.ui.state.api") as mock_api:
            mock_api.get_provider_schemas = AsyncMock(return_value={"aws": []})
            await s.load_provider_schemas()

        mock_api.get_provider_schemas.assert_not_awaited()
        assert s.provider_schemas == {}

    @pytest.mark.asyncio
    async def test_non_dict_response_coerced_to_empty_dict(self):
        """A non-dict API payload is normalised to an empty dict."""
        s = self._make_state()

        with patch("orb.ui.state.api") as mock_api:
            mock_api.get_provider_schemas = AsyncMock(return_value=["not", "a", "dict"])
            await s.load_provider_schemas()

        assert s.provider_schemas == {}

    @pytest.mark.asyncio
    async def test_exception_falls_back_to_empty_dict(self):
        """An API exception leaves provider_schemas as an empty dict."""
        s = self._make_state()
        s.provider_schemas = {"stale": []}

        with patch("orb.ui.state.api") as mock_api:
            mock_api.get_provider_schemas = AsyncMock(side_effect=RuntimeError("boom"))
            await s.load_provider_schemas()

        assert s.provider_schemas == {}
        # Guard was flipped before the fetch attempt.
        assert s._schemas_loaded is True


# ---------------------------------------------------------------------------
# AppState.poll_health — single-flight guard + loop body + finally reset
# ---------------------------------------------------------------------------


class TestPollHealth:
    """AppState.poll_health background loop."""

    def _make_state(self):
        from orb.ui.state import AppState

        TestableState = _make_testable_subclass(AppState)
        s = TestableState.__new__(TestableState)
        s.health = {}
        s.info = {}
        s.health_error = ""
        s._poll_started = False
        return s

    @pytest.mark.asyncio
    async def test_guard_returns_early_when_already_started(self):
        """poll_health is a no-op when a loop is already running."""
        s = self._make_state()
        s._poll_started = True

        with patch("orb.ui.state.api") as mock_api:
            mock_api.get_health = AsyncMock(return_value={"status": "ok"})
            mock_api.get_info = AsyncMock(return_value={})
            await s.poll_health()

        # The early return means the tick never fetched health.
        mock_api.get_health.assert_not_awaited()
        # Guard remains set (the running loop owns it).
        assert s._poll_started is True

    @pytest.mark.asyncio
    async def test_loop_ticks_then_resets_guard_on_break(self):
        """One iteration runs, then a sleep failure breaks the loop and the
        finally block resets the single-flight guard."""
        s = self._make_state()

        with (
            patch("orb.ui.state.api") as mock_api,
            patch("asyncio.sleep", new=AsyncMock(side_effect=RuntimeError("stop"))),
        ):
            mock_api.get_health = AsyncMock(return_value={"status": "ok"})
            mock_api.get_info = AsyncMock(return_value={"version": "9.9.9"})

            with pytest.raises(RuntimeError, match="stop"):
                await s.poll_health()

        # First tick executed before the sleep raised.
        assert s.health == {"status": "ok"}
        assert s.info == {"version": "9.9.9"}
        # finally block reset the guard so a remount can start a fresh loop.
        assert s._poll_started is False


# ---------------------------------------------------------------------------
# AppState.health_check_rows — non-dict detail branch (155->158)
# ---------------------------------------------------------------------------


class TestHealthCheckRowsNonDictDetail:
    """health_check_rows when a check value is not a dict."""

    def _state_with(self, health: dict):
        from orb.ui.state import AppState

        s = AppState.__new__(AppState)
        s.health = health
        s.health_error = ""
        return s

    def test_non_dict_detail_yields_unknown_status(self):
        """A non-dict check value keeps default status='unknown', message=''."""
        from orb.ui.state import AppState

        s = self._state_with(
            {
                "status": "ok",
                "checks": {
                    "weird": "just-a-string",
                    "db": {"status": "ok", "message": "fine"},
                },
            }
        )
        rows = AppState.health_check_rows(s)
        by_name = {r["name"]: r for r in rows}
        assert by_name["weird"]["status"] == "unknown"
        assert by_name["weird"]["message"] == ""
        assert by_name["db"]["status"] == "ok"

    def test_health_not_a_dict_returns_empty(self):
        """A non-dict health payload short-circuits to an empty list."""
        from orb.ui.state import AppState

        s = self._state_with([])  # type: ignore[arg-type]
        assert AppState.health_check_rows(s) == []

    def test_detail_none_values_stringified_to_defaults(self):
        """A dict detail with None status/message coerces to 'unknown'/''."""
        from orb.ui.state import AppState

        s = self._state_with({"checks": {"cache": {"status": None, "message": None}}})
        rows = AppState.health_check_rows(s)
        assert rows[0]["status"] == "unknown"
        assert rows[0]["message"] == ""


# ---------------------------------------------------------------------------
# CurrentUserState — remaining permission vars (216, 220, 224, 228)
# ---------------------------------------------------------------------------


class TestCurrentUserRemainingPermissions:
    """Permission computed vars not exercised by test_state.py."""

    def _state_with(self, permissions: list[str]):
        from orb.ui.state import CurrentUserState

        s = CurrentUserState.__new__(CurrentUserState)
        s.role = "operator"
        s.permissions = permissions
        s.username = "u"
        s.loaded = True
        return s

    def test_can_return_machines_true_when_present(self):
        from orb.ui.state import CurrentUserState

        s = self._state_with(["return_machines"])
        assert CurrentUserState.can_return_machines(s) is True

    def test_can_return_machines_false_when_absent(self):
        from orb.ui.state import CurrentUserState

        s = self._state_with([])
        assert CurrentUserState.can_return_machines(s) is False

    def test_can_cancel_request_true_when_present(self):
        from orb.ui.state import CurrentUserState

        s = self._state_with(["cancel_request"])
        assert CurrentUserState.can_cancel_request(s) is True

    def test_can_cancel_request_false_when_absent(self):
        from orb.ui.state import CurrentUserState

        s = self._state_with(["request_machines"])
        assert CurrentUserState.can_cancel_request(s) is False

    def test_can_create_template_true_when_present(self):
        from orb.ui.state import CurrentUserState

        s = self._state_with(["create_template"])
        assert CurrentUserState.can_create_template(s) is True

    def test_can_update_template_true_when_present(self):
        from orb.ui.state import CurrentUserState

        s = self._state_with(["update_template"])
        assert CurrentUserState.can_update_template(s) is True

    def test_can_update_template_false_when_absent(self):
        from orb.ui.state import CurrentUserState

        s = self._state_with(["create_template"])
        assert CurrentUserState.can_update_template(s) is False


# ---------------------------------------------------------------------------
# CurrentUserState.load — missing-field default path (partial payload)
# ---------------------------------------------------------------------------


class TestCurrentUserLoadDefaults:
    """load must apply per-field defaults when keys are absent."""

    def test_load_uses_defaults_for_missing_fields(self):
        async def _run():
            with patch("orb.ui.state.api") as mock_api:
                from orb.ui.state import CurrentUserState

                # Payload with no username/role/permissions keys.
                mock_api.get_me = AsyncMock(return_value={})
                s = CurrentUserState.__new__(CurrentUserState)
                s.username = ""
                s.role = "viewer"
                s.permissions = []
                s.loaded = False

                await CurrentUserState.load(s)

            assert s.username == ""
            assert s.role == "viewer"
            assert s.permissions == []
            assert s.loaded is True

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# provider_columns._make_formatter — invoke each closure (77-138)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _distinct_rx_primitives():
    """Patch each rx primitive with a *distinct* mock and make ``rx.cond``
    actually select an arm based on the (real Python bool) predicate.

    The shared conftest stub aliases rx.code/rx.text/rx.badge/rx.link/rx.match/
    rx.cond to the SAME MagicMock, which makes ``assert_called`` on any one of
    them trivially true.  Here each primitive is its own mock so the SELECTED
    component's identity is meaningful, and ``rx.cond`` returns the true arm
    when the predicate is truthy and the false arm otherwise — so we can assert
    the empty-value branch really renders the em-dash node.
    """
    import reflex as rx

    def _cond(pred, true_arm, false_arm):
        return true_arm if pred else false_arm

    patches = {
        "cond": MagicMock(side_effect=_cond),
        "code": MagicMock(name="rx.code"),
        "text": MagicMock(name="rx.text"),
        "badge": MagicMock(name="rx.badge"),
        "link": MagicMock(name="rx.link"),
        "match": MagicMock(name="rx.match"),
        "color": MagicMock(name="rx.color"),
    }
    with contextlib.ExitStack() as stack:
        for name, mock in patches.items():
            stack.enter_context(patch.object(rx, name, mock))
        yield patches


class TestMakeFormatterClosures:
    """Directly invoke each kind's formatter closure with a row dict.

    Under the rx stub every rx primitive is a MagicMock, so invoking the
    closure exercises the closure body (and its em-dash cond branch) even
    though no real component tree is produced.
    """

    def _make(self, descriptor):
        from orb.ui.components.provider_columns import _make_formatter

        return _make_formatter(descriptor)

    def test_code_kind_present_value(self):
        fmt = self._make({"kind": "code", "key": "fleet"})
        with _distinct_rx_primitives() as rxp:
            result = fmt({"fleet": "fleet-123"})
        # Non-empty value selects the rx.code arm (not the em-dash text arm).
        rxp["code"].assert_called_once_with("fleet-123", size="1")
        assert result is rxp["code"].return_value
        assert result is not rxp["text"].return_value

    def test_code_kind_empty_value_uses_emdash(self):
        fmt = self._make({"kind": "code", "key": "fleet"})
        with _distinct_rx_primitives() as rxp:
            result = fmt({"fleet": ""})
        # cond predicate is the truth-test on the value, and the FALSE arm
        # (the em-dash text node) is the one actually selected.
        # Both arms are constructed eagerly (rx.cond does not lazily branch),
        # so the meaningful check is the predicate and which arm rx.cond
        # actually returns — here the FALSE (em-dash) arm.
        pred, _true_arm, false_arm = rxp["cond"].call_args.args
        assert pred is False
        assert false_arm is rxp["text"].return_value
        assert result is rxp["text"].return_value
        rxp["text"].assert_called_once_with("—", size="1", color=rxp["color"].return_value)

    def test_badge_kind_with_color_map_invokes_match(self):
        fmt = self._make(
            {
                "kind": "badge",
                "key": "price",
                "badge_color_map": {"spot": "orange", "ondemand": "blue"},
            }
        )
        with _distinct_rx_primitives() as rxp:
            result = fmt({"price": "spot"})
        # The color-map path forwards the value plus every (value, color) tuple
        # and a "gray" fallback into rx.match, and selects the badge arm.
        rxp["match"].assert_called_once_with(
            "spot", ("spot", "orange"), ("ondemand", "blue"), "gray"
        )
        rxp["badge"].assert_called_once_with(
            "spot", variant="soft", color_scheme=rxp["match"].return_value, size="1"
        )
        assert result is rxp["badge"].return_value

    def test_badge_kind_with_color_map_empty_value(self):
        fmt = self._make(
            {
                "kind": "badge",
                "key": "price",
                "badge_color_map": {"spot": "orange"},
            }
        )
        with _distinct_rx_primitives() as rxp:
            result = fmt({"price": ""})
        pred, _true_arm, false_arm = rxp["cond"].call_args.args
        assert pred is False
        assert false_arm is rxp["text"].return_value
        assert result is rxp["text"].return_value
        rxp["text"].assert_called_once_with("—", size="1", color=rxp["color"].return_value)

    def test_badge_kind_without_color_map_uses_gray(self):
        import reflex as rx

        rx.badge.reset_mock()
        fmt = self._make({"kind": "badge", "key": "status"})
        result = fmt({"status": "active"})
        assert result is not None
        rx.badge.assert_called()
        gray_calls = [c for c in rx.badge.call_args_list if c.kwargs.get("color_scheme") == "gray"]
        assert len(gray_calls) >= 1

    def test_count_kind_present_value(self):
        import reflex as rx

        rx.badge.reset_mock()
        fmt = self._make({"kind": "count", "key": "n"})
        result = fmt({"n": "5"})
        assert result is not None
        blue_calls = [c for c in rx.badge.call_args_list if c.kwargs.get("color_scheme") == "blue"]
        assert len(blue_calls) >= 1

    def test_count_kind_empty_value_uses_emdash(self):
        fmt = self._make({"kind": "count", "key": "n"})
        with _distinct_rx_primitives() as rxp:
            result = fmt({"n": ""})
        pred, _true_arm, false_arm = rxp["cond"].call_args.args
        assert pred is False
        assert false_arm is rxp["text"].return_value
        assert result is rxp["text"].return_value
        rxp["text"].assert_called_once_with("—", size="1", color=rxp["color"].return_value)

    def test_link_kind_present_value(self):
        import reflex as rx

        rx.link.reset_mock()
        fmt = self._make({"kind": "link", "key": "url"})
        result = fmt({"url": "https://example.com"})
        assert result is not None
        rx.link.assert_called()
        link_calls = [c for c in rx.link.call_args_list if c.kwargs.get("is_external") is True]
        assert len(link_calls) >= 1

    def test_link_kind_empty_value_uses_emdash(self):
        fmt = self._make({"kind": "link", "key": "url"})
        with _distinct_rx_primitives() as rxp:
            result = fmt({"url": ""})
        pred, _true_arm, false_arm = rxp["cond"].call_args.args
        assert pred is False
        assert false_arm is rxp["text"].return_value
        assert result is rxp["text"].return_value
        rxp["text"].assert_called_once_with("—", size="1", color=rxp["color"].return_value)

    def test_text_kind_default_present_value(self):
        import reflex as rx

        rx.text.reset_mock()
        fmt = self._make({"kind": "text", "key": "name"})
        result = fmt({"name": "hello"})
        assert result is not None
        val_calls = [c for c in rx.text.call_args_list if c.args and c.args[0] == "hello"]
        assert len(val_calls) >= 1

    def test_unknown_kind_falls_back_to_text(self):
        """An unrecognised kind takes the text/default branch."""
        import reflex as rx

        rx.text.reset_mock()
        fmt = self._make({"kind": "mystery", "key": "name"})
        result = fmt({"name": "value"})
        assert result is not None
        val_calls = [c for c in rx.text.call_args_list if c.args and c.args[0] == "value"]
        assert len(val_calls) >= 1

    def test_missing_kind_defaults_to_text(self):
        """No kind key → defaults to 'text' rendering."""
        import reflex as rx

        rx.text.reset_mock()
        fmt = self._make({"key": "name"})
        result = fmt({"name": "plain"})
        assert result is not None


# ---------------------------------------------------------------------------
# provider_columns skip branches — build (209) + resolve (258, 261, 267)
# ---------------------------------------------------------------------------


class TestBuildProviderColumnsSkipBranches:
    """build_provider_columns skips malformed descriptors."""

    def _build(self, schemas, resource_type, active_provider):
        from orb.ui.components.provider_columns import build_provider_columns

        return build_provider_columns(schemas, resource_type, active_provider)

    def test_non_dict_descriptor_in_list_is_skipped(self):
        """A non-dict entry inside a provider's descriptor list is skipped (209)."""
        schemas = {
            "aws": [
                "not-a-dict",
                {
                    "key": "aws_x",
                    "path": "provider_data.x",
                    "label": "X",
                    "kind": "text",
                    "resource_type": "machines",
                },
            ]
        }
        result = self._build(schemas, "machines", None)
        keys = [c.key for c in result]
        assert keys == ["aws_x"]

    def test_schemas_not_a_dict_returns_empty(self):
        result = self._build(["not-a-dict"], "machines", None)  # type: ignore[arg-type]
        assert result == []


class TestResolveProviderRowFieldsSkipBranches:
    """resolve_provider_row_fields skips malformed input."""

    def _resolve(self, row, schemas, resource_type, active_provider):
        from orb.ui.components.provider_columns import resolve_provider_row_fields

        return resolve_provider_row_fields(row, schemas, resource_type, active_provider)

    def test_non_list_descriptors_value_is_skipped(self):
        """A provider mapped to a non-list value is skipped (258)."""
        schemas = {"aws": "not-a-list"}
        result = self._resolve({"provider_data": {}}, schemas, "machines", None)  # type: ignore[dict-item]
        assert result == {}

    def test_non_dict_descriptor_in_list_is_skipped(self):
        """A non-dict descriptor inside the list is skipped (261)."""
        schemas = {
            "aws": [
                12345,
                {
                    "key": "aws_x",
                    "path": "provider_data.x",
                    "label": "X",
                    "kind": "text",
                    "resource_type": "machines",
                },
            ]
        }
        row = {"provider_data": {"x": "v"}}
        result = self._resolve(row, schemas, "machines", None)  # type: ignore[list-item]
        assert result == {"aws_x": "v"}

    def test_descriptor_with_empty_key_is_skipped(self):
        """A descriptor whose key resolves to empty string is skipped (267)."""
        schemas = {
            "aws": [
                {
                    "key": "",
                    "path": "provider_data.x",
                    "label": "No Key",
                    "kind": "text",
                    "resource_type": "machines",
                }
            ]
        }
        row = {"provider_data": {"x": "v"}}
        result = self._resolve(row, schemas, "machines", None)
        assert result == {}

    def test_schemas_not_a_dict_returns_empty(self):
        result = self._resolve({}, ["nope"], "machines", None)  # type: ignore[arg-type]
        assert result == {}

    def test_path_defaults_to_key_when_absent(self):
        """When a descriptor has no path, the key is used as the lookup path."""
        schemas = {
            "aws": [
                {
                    "key": "top_level",
                    "label": "Top",
                    "kind": "text",
                    "resource_type": "machines",
                }
            ]
        }
        row = {"top_level": "direct-value"}
        result = self._resolve(row, schemas, "machines", None)
        assert result == {"top_level": "direct-value"}
