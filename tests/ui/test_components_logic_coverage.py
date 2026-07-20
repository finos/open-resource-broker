"""Logic-only tests for orb.ui.components.

These target the *Python* logic inside the component modules — event
handlers, computed vars (with the @rx.var decorator stripped by the
conftest stub), data transforms, formatters, and prop computations. We
never boot a Reflex server or render real component trees; where a helper
returns an ``rx.*`` tree we only assert the Python decisions that feed it.

Import rule (see tests/ui/conftest.py): all ``orb.ui.*`` imports happen
inside test functions/fixtures, after the rx stub is installed.

Some component modules call ``rx.*`` attributes not present in the shared
stub (popover, dialog, select, mobile_only, call_script, redirect, ...).
We augment the already-installed stub additively at module import time —
we do NOT edit the shared conftest.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Augment the shared rx stub with attributes the component render paths need.
# Additive only — never mutate existing stub entries.
# ---------------------------------------------------------------------------
import reflex as rx  # noqa: E402  (stub already installed by conftest)

for _attr in [
    "popover",
    "dialog",
    "select",
    "mobile_only",
    "tablet_and_desktop",
    "noop",
    "call_script",
    "redirect",
    "el",
    "color_mode",
    "html",
    "color",
]:
    if not hasattr(rx, _attr):
        setattr(rx, _attr, MagicMock(name=f"rx.{_attr}"))

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Harness: make an async-context-manager-capable subclass for background
# event handlers that use ``async with self:``.
# ---------------------------------------------------------------------------


def _testable(StateClass):
    class _T(StateClass):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    _T.__name__ = f"Testable{StateClass.__name__}"
    _T.__qualname__ = _T.__name__
    return _T


# ===========================================================================
# machine_quick_view.py — _fmt_unix_ts formatter
# ===========================================================================


class TestFmtUnixTs:
    def test_none_renders_em_dash(self):
        from orb.ui.components.machine_quick_view import _fmt_unix_ts

        assert _fmt_unix_ts(None) == "—"

    def test_iso_string_formatted_as_utc(self):
        from orb.ui.components.machine_quick_view import _fmt_unix_ts

        assert _fmt_unix_ts("2023-11-14T22:13:20Z") == "2023-11-14 22:13 UTC"

    def test_unix_seconds_int_formatted(self):
        from orb.ui.components.machine_quick_view import _fmt_unix_ts

        # 1700000000 == 2023-11-14 22:13:20 UTC
        assert _fmt_unix_ts(1700000000) == "2023-11-14 22:13 UTC"

    def test_unix_milliseconds_int_formatted(self):
        from orb.ui.components.machine_quick_view import _fmt_unix_ts

        # >= 1e12 is treated as already-milliseconds.
        assert _fmt_unix_ts(1700000000000) == "2023-11-14 22:13 UTC"

    def test_unparseable_string_returned_as_is(self):
        from orb.ui.components.machine_quick_view import _fmt_unix_ts

        assert _fmt_unix_ts("not-a-date") == "not-a-date"


# ===========================================================================
# machine_quick_view.py — computed vars (rx.var decorator stripped by stub)
# ===========================================================================


class TestMachineQuickViewComputedVars:
    def _state(self, machine: dict):
        from orb.ui.components.machine_quick_view import MachineQuickViewState

        s = MachineQuickViewState.__new__(MachineQuickViewState)
        s.selected_machine = machine
        return s

    def test_launch_fmt_reads_launch_time(self):
        s = self._state({"launch_time": 1700000000})
        assert s.selected_machine_launch_fmt() == "2023-11-14 22:13 UTC"

    def test_term_fmt_reads_termination_time(self):
        s = self._state({"termination_time": None})
        assert s.selected_machine_term_fmt() == "—"

    def test_tags_text_empty_returns_empty_object(self):
        s = self._state({"tags": None})
        assert s.selected_machine_tags_text() == "{}"

    def test_tags_text_serialises_dict(self):
        s = self._state({"tags": {"Name": "web"}})
        out = s.selected_machine_tags_text()
        assert '"Name": "web"' in out

    def test_tags_text_falls_back_to_str_on_unserialisable(self):
        s = self._state({"tags": {object()}})  # a set of an object -> not JSON
        out = s.selected_machine_tags_text()
        assert out != "{}"
        assert isinstance(out, str)

    def test_health_text_none_returns_null_literal(self):
        s = self._state({"health_checks": None})
        assert s.selected_machine_health_text() == "null"

    def test_health_text_serialises_payload(self):
        s = self._state({"health_checks": {"status": "ok"}})
        assert '"status": "ok"' in s.selected_machine_health_text()

    def test_provider_data_text_empty_returns_empty_object(self):
        s = self._state({"provider_data": {}})
        assert s.selected_machine_provider_data_text() == "{}"

    def test_provider_data_text_serialises(self):
        s = self._state({"provider_data": {"az": "us-east-1a"}})
        assert '"az": "us-east-1a"' in s.selected_machine_provider_data_text()

    def test_sg_text_empty_returns_em_dash(self):
        s = self._state({"security_group_ids": []})
        assert s.selected_machine_sg_text() == "—"

    def test_sg_text_joins_ids(self):
        s = self._state({"security_group_ids": ["sg-1", "sg-2"]})
        assert s.selected_machine_sg_text() == "sg-1, sg-2"

    def test_sg_text_missing_key_returns_em_dash(self):
        s = self._state({})
        assert s.selected_machine_sg_text() == "—"


# ===========================================================================
# machine_quick_view.py — synchronous event handlers
# ===========================================================================


class TestMachineQuickViewSyncHandlers:
    def _state(self):
        from orb.ui.components.machine_quick_view import (
            _EMPTY_MACHINE,
            MachineQuickViewState,
        )

        s = MachineQuickViewState.__new__(MachineQuickViewState)
        s.drawer_open = False
        s.selected_machine = dict(_EMPTY_MACHINE)
        s.syncing_drawer = False
        s.last_sync_time = ""
        s.sync_error = ""
        s.live_poll_enabled = "false"
        return s

    def test_toggle_live_poll_true_sets_string_true(self):
        s = self._state()
        s.toggle_live_poll(True)
        assert s.live_poll_enabled == "true"

    def test_toggle_live_poll_false_sets_string_false(self):
        s = self._state()
        s.live_poll_enabled = "true"
        s.toggle_live_poll(False)
        assert s.live_poll_enabled == "false"

    def test_close_drawer_clears_open(self):
        s = self._state()
        s.drawer_open = True
        s.close_drawer()
        assert s.drawer_open is False

    def test_set_drawer_open_sets_value(self):
        s = self._state()
        s.set_drawer_open(True)
        assert s.drawer_open is True
        s.set_drawer_open(False)
        assert s.drawer_open is False


# ===========================================================================
# machine_quick_view.py — async event handlers
# ===========================================================================


class TestMachineQuickViewOpenDrawer:
    def _state(self):
        from orb.ui.components.machine_quick_view import (
            _EMPTY_MACHINE,
            MachineQuickViewState,
        )

        T = _testable(MachineQuickViewState)
        s = T.__new__(T)
        s.drawer_open = False
        s.selected_machine = dict(_EMPTY_MACHINE)
        s.syncing_drawer = False
        s.last_sync_time = ""
        s.sync_error = ""
        s.live_poll_enabled = "false"
        return s

    async def _drain(self, gen):
        return [x async for x in gen]

    def test_open_drawer_loads_full_machine_and_yields_poll(self):
        from orb.ui.components.machine_quick_view import MachineQuickViewState

        s = self._state()
        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.get_machine = AsyncMock(return_value={"machine_id": "i-1", "status": "running"})
            yielded = asyncio.run(self._drain(s.open_drawer({"machine_id": "i-1", "name": "web"})))

        assert s.drawer_open is True
        assert s.selected_machine["machine_id"] == "i-1"
        assert s.selected_machine["status"] == "running"
        assert s.syncing_drawer is False
        assert MachineQuickViewState.poll_drawer_machine in yielded

    def test_open_drawer_unwraps_machines_list_payload(self):
        s = self._state()
        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.get_machine = AsyncMock(
                return_value={"machines": [{"machine_id": "i-2", "status": "pending"}]}
            )
            asyncio.run(self._drain(s.open_drawer({"machine_id": "i-2"})))

        assert s.selected_machine["machine_id"] == "i-2"
        assert s.selected_machine["status"] == "pending"

    def test_open_drawer_no_machine_id_skips_api(self):
        s = self._state()
        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.get_machine = AsyncMock()
            asyncio.run(self._drain(s.open_drawer({})))
        api.get_machine.assert_not_called()
        assert s.drawer_open is True

    def test_open_drawer_api_error_sets_sync_error(self):
        s = self._state()
        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.get_machine = AsyncMock(side_effect=RuntimeError("boom"))
            asyncio.run(self._drain(s.open_drawer({"machine_id": "i-3"})))
        assert "Failed to load full machine details" in s.sync_error
        assert s.syncing_drawer is False


class TestMachineQuickViewSyncDrawer:
    def _state(self, machine_id="i-1"):
        from orb.ui.components.machine_quick_view import (
            _EMPTY_MACHINE,
            MachineQuickViewState,
        )

        s = MachineQuickViewState.__new__(MachineQuickViewState)
        s.selected_machine = {**_EMPTY_MACHINE, "machine_id": machine_id}
        s.syncing_drawer = False
        s.last_sync_time = ""
        s.sync_error = ""
        return s

    def test_sync_no_machine_id_no_op(self):
        s = self._state(machine_id="")
        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.sync_machine = AsyncMock()
            asyncio.run(s.sync_drawer_machine())
        api.sync_machine.assert_not_called()

    def test_sync_success_updates_machine_and_timestamp(self):
        s = self._state()
        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.sync_machine = AsyncMock(
                return_value={"machine_id": "i-1", "status": "running", "synced": True}
            )
            asyncio.run(s.sync_drawer_machine())
        assert s.selected_machine["status"] == "running"
        # synced/sync_error control keys stripped from stored machine
        assert "synced" not in s.selected_machine
        assert "sync_error" not in s.selected_machine
        assert s.last_sync_time != ""
        assert s.syncing_drawer is False

    def test_sync_provider_reported_failure_sets_sync_error(self):
        s = self._state()
        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.sync_machine = AsyncMock(
                return_value={
                    "machine_id": "i-1",
                    "synced": False,
                    "sync_error": "provider timeout",
                }
            )
            asyncio.run(s.sync_drawer_machine())
        assert s.sync_error == "provider timeout"

    def test_sync_exception_sets_sync_error(self):
        s = self._state()
        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.sync_machine = AsyncMock(side_effect=RuntimeError("down"))
            asyncio.run(s.sync_drawer_machine())
        assert "Sync failed" in s.sync_error
        assert s.syncing_drawer is False

    def test_return_drawer_machine_closes_drawer(self):
        from orb.ui.components.machine_quick_view import MachineQuickViewState

        s = MachineQuickViewState.__new__(MachineQuickViewState)
        s.drawer_open = True
        asyncio.run(s.return_drawer_machine())
        assert s.drawer_open is False


class TestMachineQuickViewPoll:
    def _state(self, **kw):
        from orb.ui.components.machine_quick_view import (
            _EMPTY_MACHINE,
            MachineQuickViewState,
        )

        T = _testable(MachineQuickViewState)
        s = T.__new__(T)
        s.drawer_open = kw.get("drawer_open", True)
        s.selected_machine = {**_EMPTY_MACHINE, **kw.get("machine", {})}
        s.syncing_drawer = kw.get("syncing_drawer", False)
        s.live_poll_enabled = kw.get("live_poll_enabled", "false")
        s.last_sync_time = ""
        s.sync_error = ""
        return s

    def test_poll_returns_immediately_when_no_machine_id(self):
        s = self._state(machine={"machine_id": ""})
        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.get_machine = AsyncMock()
            asyncio.run(s.poll_drawer_machine())
        api.get_machine.assert_not_called()

    def test_poll_returns_immediately_when_drawer_closed(self):
        s = self._state(drawer_open=False, machine={"machine_id": "i-1"})
        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.get_machine = AsyncMock()
            asyncio.run(s.poll_drawer_machine())
        api.get_machine.assert_not_called()

    def test_poll_reads_machine_and_stops_on_terminal_status(self):
        s = self._state(
            machine={"machine_id": "i-1", "status": "running"},
            live_poll_enabled="true",
        )
        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.get_machine = AsyncMock(return_value={"machine_id": "i-1", "status": "terminated"})
            # asyncio.sleep is patched to a no-op so the loop is deterministic.
            with patch(
                "orb.ui.components.machine_quick_view.asyncio.sleep",
                new=AsyncMock(),
            ):
                asyncio.run(s.poll_drawer_machine())
        api.get_machine.assert_awaited()
        assert s.selected_machine["status"] == "terminated"

    def test_poll_paused_when_live_disabled_then_machine_changes(self):
        # live_poll disabled -> loop sleeps without hitting API; we flip the
        # selected machine id during the (patched) sleep so the loop exits.
        s = self._state(
            machine={"machine_id": "i-1", "status": "running"},
            live_poll_enabled="false",
        )

        async def _sleep(_secs):
            # Change machine id so the next loop guard returns.
            s.selected_machine = {**s.selected_machine, "machine_id": "i-OTHER"}

        with patch("orb.ui.components.machine_quick_view.api") as api:
            api.get_machine = AsyncMock()
            with patch(
                "orb.ui.components.machine_quick_view.asyncio.sleep",
                new=_sleep,
            ):
                asyncio.run(s.poll_drawer_machine())
        api.get_machine.assert_not_called()


# ===========================================================================
# request_modal.py — event handlers, computed var
# ===========================================================================


class TestRequestModalOpenHandlers:
    def _state(self):
        from orb.ui.components.request_modal import RequestModalState

        T = _testable(RequestModalState)
        s = T.__new__(T)
        s.open = False
        s.template_id = ""
        s.count = "1"
        s.loading = False
        s.error = ""
        s.last_request_id = ""
        s.picker_mode = False
        s.available_templates = []
        s.templates_loading = False
        return s

    def test_open_for_presets_template_and_disables_picker(self):
        s = self._state()
        s.last_request_id = "stale"
        asyncio.run(s.open_for("tpl-42"))
        assert s.template_id == "tpl-42"
        assert s.count == "1"
        assert s.picker_mode is False
        assert s.open is True
        assert s.last_request_id == ""

    def test_open_picker_loads_templates_and_defaults_first(self):
        s = self._state()
        with patch("orb.ui.components.request_modal.api") as api:
            api.list_templates = AsyncMock(
                return_value={
                    "templates": [
                        {"template_id": "a", "name": "Alpha"},
                        {"template_id": "b", "name": "Beta"},
                    ]
                }
            )
            asyncio.run(s.open_picker())
        assert s.picker_mode is True
        assert s.open is True
        assert len(s.available_templates) == 2
        assert s.template_id == "a"  # first template auto-selected
        assert s.templates_loading is False

    def test_open_picker_empty_templates_leaves_template_id_blank(self):
        s = self._state()
        with patch("orb.ui.components.request_modal.api") as api:
            api.list_templates = AsyncMock(return_value={"templates": []})
            asyncio.run(s.open_picker())
        assert s.available_templates == []
        assert s.template_id == ""

    def test_open_picker_api_error_sets_error(self):
        s = self._state()
        with patch("orb.ui.components.request_modal.api") as api:
            api.list_templates = AsyncMock(side_effect=RuntimeError("nope"))
            asyncio.run(s.open_picker())
        assert "Failed to load templates" in s.error
        assert s.templates_loading is False

    def test_set_template_id(self):
        s = self._state()
        s.set_template_id("tpl-x")
        assert s.template_id == "tpl-x"

    def test_set_count(self):
        s = self._state()
        asyncio.run(s.set_count("7"))
        assert s.count == "7"

    def test_close_clears_open_and_error(self):
        s = self._state()
        s.open = True
        s.error = "boom"
        asyncio.run(s.close())
        assert s.open is False
        assert s.error == ""

    def test_dismiss_success_banner_clears_last_request_id(self):
        s = self._state()
        s.last_request_id = "req-1"
        s.dismiss_success_banner()
        assert s.last_request_id == ""

    def test_view_request_clears_banner_and_redirects(self):
        s = self._state()
        s.last_request_id = "req-1"
        rx.redirect = MagicMock(return_value="REDIRECT")
        out = s.view_request()
        assert s.last_request_id == ""
        assert out == "REDIRECT"
        rx.redirect.assert_called_once_with("/requests")


class TestRequestModalTemplateOptions:
    def _state(self, templates):
        from orb.ui.components.request_modal import RequestModalState

        s = RequestModalState.__new__(RequestModalState)
        s.available_templates = templates
        return s

    def test_uses_name_as_label(self):
        s = self._state([{"template_id": "a", "name": "Alpha"}])
        assert s.template_options() == [{"label": "Alpha", "value": "a"}]

    def test_falls_back_to_id_when_name_blank(self):
        s = self._state([{"template_id": "a", "name": "   "}])
        assert s.template_options() == [{"label": "a", "value": "a"}]

    def test_skips_entries_without_id(self):
        s = self._state([{"name": "NoId"}, {"template_id": "b", "name": "Beta"}])
        assert s.template_options() == [{"label": "Beta", "value": "b"}]

    def test_empty_list_returns_empty(self):
        s = self._state([])
        assert s.template_options() == []


class TestRequestModalSubmit:
    def _state(self, count="1", template_id="tpl-1", loading=False):
        from orb.ui.components.request_modal import RequestModalState

        T = _testable(RequestModalState)
        s = T.__new__(T)
        s.open = True
        s.template_id = template_id
        s.count = count
        s.loading = loading
        s.error = ""
        s.last_request_id = ""
        return s

    def test_submit_reentrancy_guard_when_already_loading(self):
        s = self._state(loading=True)
        with patch("orb.ui.components.request_modal.api") as api:
            api.request_machines = AsyncMock()
            out = asyncio.run(s.submit())
        assert out is None
        api.request_machines.assert_not_called()

    def test_submit_non_integer_count_sets_error(self):
        s = self._state(count="abc")
        with patch("orb.ui.components.request_modal.api") as api:
            api.request_machines = AsyncMock()
            out = asyncio.run(s.submit())
        assert out is None
        assert s.error == "Count must be a positive integer."
        api.request_machines.assert_not_called()

    def test_submit_zero_count_sets_error(self):
        s = self._state(count="0")
        with patch("orb.ui.components.request_modal.api") as api:
            api.request_machines = AsyncMock()
            out = asyncio.run(s.submit())
        assert out is None
        assert s.error == "Count must be at least 1."
        api.request_machines.assert_not_called()

    def test_submit_success_closes_and_sets_request_id(self):
        s = self._state(count="3", template_id="tpl-9")
        rx.call_script = MagicMock(return_value="SCRIPT")
        with patch("orb.ui.components.request_modal.api") as api:
            api.request_machines = AsyncMock(return_value={"request_id": "req-77"})
            out = asyncio.run(s.submit())
        api.request_machines.assert_awaited_once_with({"template_id": "tpl-9", "count": 3})
        assert s.last_request_id == "req-77"
        assert s.open is False
        assert s.loading is False
        assert out == "SCRIPT"

    def test_submit_api_failure_sets_error_and_clears_loading(self):
        s = self._state(count="2")
        with patch("orb.ui.components.request_modal.api") as api:
            api.request_machines = AsyncMock(side_effect=RuntimeError("timeout"))
            out = asyncio.run(s.submit())
        assert out is None
        assert "Request failed" in s.error
        assert s.loading is False


# ===========================================================================
# list_grid_view.py — ColumnDef + internal cell/header helpers
# ===========================================================================


class TestColumnDef:
    def test_defaults(self):
        from orb.ui.components.list_grid_view import ColumnDef

        c = ColumnDef("status", "Status")
        assert c.key == "status"
        assert c.title == "Status"
        assert c.formatter is None
        assert c.default_visible is True
        assert c.lockable is False
        assert c.sortable is False
        assert c.width is None
        assert c.align == "start"
        assert c.header_renderer is None

    def test_frozen_and_hashable_ignoring_callables(self):
        from orb.ui.components.list_grid_view import ColumnDef

        # formatter/header_renderer are compare=False, hash=False so two
        # ColumnDefs with different callables but same scalar fields are equal.
        c1 = ColumnDef("k", "T", formatter=lambda r: r)
        c2 = ColumnDef("k", "T", formatter=lambda r: r)
        assert c1 == c2
        assert hash(c1) == hash(c2)


class TestCellContent:
    def test_formatter_invoked_with_row(self):
        from orb.ui.components.list_grid_view import ColumnDef, _cell_content

        seen = {}

        def fmt(row):
            seen["row"] = row
            return "RENDERED"

        col = ColumnDef("k", "T", formatter=fmt)
        assert _cell_content(col, {"k": "v"}) == "RENDERED"
        assert seen["row"] == {"k": "v"}

    def test_no_formatter_falls_back_to_text(self):
        from orb.ui.components.list_grid_view import ColumnDef, _cell_content

        col = ColumnDef("k", "T")
        # rx.text is a MagicMock in the stub — assert it was called with the
        # row-keyed value.
        rx.text.reset_mock()
        _cell_content(col, {"k": "value"})
        rx.text.assert_called_once()
        assert rx.text.call_args.args[0] == "value"


class TestAlignMapping:
    def test_align_to_text_align_table(self):
        from orb.ui.components.list_grid_view import _ALIGN_TO_TEXT_ALIGN

        assert _ALIGN_TO_TEXT_ALIGN == {
            "start": "left",
            "center": "center",
            "end": "right",
        }


class TestHeaderCell:
    def test_custom_header_renderer_takes_priority(self):
        from orb.ui.components.list_grid_view import ColumnDef, _header_cell

        called = {}

        def hr():
            called["hit"] = True
            return "HEADER"

        col = ColumnDef("_select", "", header_renderer=hr)
        rx.table.column_header_cell.reset_mock()
        _header_cell(col, MagicMock(), MagicMock(), MagicMock())
        assert called.get("hit") is True
        rx.table.column_header_cell.assert_called()

    def test_plain_header_when_not_sortable(self):
        from orb.ui.components.list_grid_view import ColumnDef, _header_cell

        col = ColumnDef("status", "Status")
        rx.table.column_header_cell.reset_mock()
        _header_cell(col, MagicMock(), MagicMock(), None)
        # Plain header path passes the title through column_header_cell.
        rx.table.column_header_cell.assert_called()
        assert rx.table.column_header_cell.call_args.args[0] == "Status"

    def test_plain_header_applies_width_and_align(self):
        from orb.ui.components.list_grid_view import ColumnDef, _header_cell

        col = ColumnDef("status", "Status", width="120px", align="end")
        rx.table.column_header_cell.reset_mock()
        _header_cell(col, MagicMock(), MagicMock(), None)
        kwargs = rx.table.column_header_cell.call_args.kwargs
        assert kwargs.get("width") == "120px"
        assert kwargs.get("text_align") == "right"

    def test_sortable_without_width_returns_sortable_header(self):
        from orb.ui.components import list_grid_view as lg
        from orb.ui.components.list_grid_view import ColumnDef, _header_cell

        col = ColumnDef("created_at", "Created", sortable=True)
        with patch.object(lg, "sortable_header", return_value="SORTABLE") as sh:
            out = _header_cell(col, MagicMock(), MagicMock(), MagicMock())
        assert out == "SORTABLE"
        sh.assert_called_once()
        assert sh.call_args.kwargs["col_key"] == "created_at"

    def test_sortable_with_width_builds_custom_cell(self):
        from orb.ui.components import list_grid_view as lg
        from orb.ui.components.list_grid_view import ColumnDef, _header_cell

        col = ColumnDef("created_at", "Created", sortable=True, width="150px")
        on_sort = MagicMock(return_value="SORTEVT")
        rx.table.column_header_cell.reset_mock()
        with patch.object(lg, "sortable_header"):
            _header_cell(col, MagicMock(), MagicMock(), on_sort)
        # With an explicit width the code builds its own header cell (with the
        # width applied) rather than returning the sortable_header result.
        rx.table.column_header_cell.assert_called()
        assert rx.table.column_header_cell.call_args.kwargs.get("width") == "150px"
        on_sort.assert_called_once_with("created_at")


class TestDataCell:
    def test_default_vertical_align_middle(self):
        from orb.ui.components.list_grid_view import ColumnDef, _data_cell

        col = ColumnDef("status", "Status")
        rx.table.cell.reset_mock()
        _data_cell(col, {"status": "running"})
        kwargs = rx.table.cell.call_args.kwargs
        assert kwargs.get("vertical_align") == "middle"
        assert "width" not in kwargs
        assert "text_align" not in kwargs

    def test_width_and_align_end_applied(self):
        from orb.ui.components.list_grid_view import ColumnDef, _data_cell

        col = ColumnDef("actions", "Actions", width="80px", align="end")
        rx.table.cell.reset_mock()
        _data_cell(col, {"actions": ""})
        kwargs = rx.table.cell.call_args.kwargs
        assert kwargs.get("width") == "80px"
        assert kwargs.get("text_align") == "right"


class TestListGridViewComposition:
    def test_list_grid_view_fences_only_non_lockable_columns(self):
        from orb.ui.components.list_grid_view import ColumnDef, list_grid_view

        cols = [
            ColumnDef("id", "ID", lockable=True),
            ColumnDef("status", "Status", sortable=True),
            ColumnDef("aws_price", "AWS Price"),
        ]
        vc = MagicMock()
        vc.contains = MagicMock(return_value=True)
        list_grid_view(
            rows=MagicMock(),
            columns=cols,
            view_mode=MagicMock(),
            visible_columns=vc,
            sort_key=MagicMock(),
            sort_dir=MagicMock(),
            card_renderer=lambda r: r,
            on_row_click=MagicMock(return_value="CLICK"),
            on_sort=MagicMock(return_value="SORT"),
        )
        # Runtime visibility is gated by ``visible_columns.contains(",key,")``
        # fenced membership for every NON-lockable column (both the row cell
        # and the header cell), and the lockable "id" column is never fenced.
        fenced = {c.args[0] for c in vc.contains.call_args_list}
        assert fenced == {",status,", ",aws_price,"}
        assert ",id," not in fenced

    def test_list_grid_view_switches_view_mode_on_desktop(self):
        from orb.ui.components.list_grid_view import ColumnDef, list_grid_view

        cols = [ColumnDef("status", "Status")]
        vc = MagicMock()
        vc.contains = MagicMock(return_value=True)
        view_mode = MagicMock()

        cond_calls: list[tuple] = []

        def _cond(*args, **kwargs):
            cond_calls.append(args)
            return ("COND", len(cond_calls))

        with (
            patch.object(rx, "cond", MagicMock(side_effect=_cond)),
            patch.object(rx, "box", MagicMock(name="box")) as box,
            patch.object(rx, "grid", MagicMock(name="grid")) as grid,
            patch.object(rx, "mobile_only", MagicMock(name="mobile_only")) as mobile_only,
            patch.object(
                rx, "tablet_and_desktop", MagicMock(name="tablet_and_desktop")
            ) as tablet_and_desktop,
            patch.object(rx, "fragment", MagicMock(name="fragment")) as fragment,
        ):
            list_grid_view(
                rows=MagicMock(),
                columns=cols,
                view_mode=view_mode,
                visible_columns=vc,
                sort_key=MagicMock(),
                sort_dir=MagicMock(),
                card_renderer=lambda r: r,
            )

        # The desktop branch switches between the table (box) list view and
        # the card grid view on ``view_mode == "list"``; mobile is grid-only.
        desktop_pred, desktop_list_arm, desktop_grid_arm = cond_calls[-1]
        assert desktop_pred == (view_mode == "list")
        assert desktop_list_arm is box.return_value
        assert desktop_grid_arm is grid.return_value
        # Mobile forces the grid view regardless of preference.
        assert mobile_only.call_args.args[0] is grid.return_value
        # Desktop wrapper wraps the view-mode cond; fragment composes both.
        assert tablet_and_desktop.call_args.args[0] == ("COND", len(cond_calls))
        assert fragment.call_args.args == (
            mobile_only.return_value,
            tablet_and_desktop.return_value,
        )


# ===========================================================================
# column_picker.py — grouping and toggle wiring
# ===========================================================================


class TestColumnPicker:
    def _cols(self):
        from orb.ui.components.list_grid_view import ColumnDef

        return [
            ColumnDef("id", "ID", lockable=True),
            ColumnDef("status", "Status"),
            ColumnDef("aws_price", "AWS Price"),
        ]

    def test_lockable_columns_excluded_from_toggles(self):
        from orb.ui.components.column_picker import column_picker

        vc = MagicMock()
        vc.contains = MagicMock(return_value=True)
        on_toggle = MagicMock()
        column_picker(self._cols(), vc, on_toggle)
        toggled_keys = [c.args[0] for c in on_toggle.call_args_list]
        # id is lockable -> never offered as a toggle.
        assert "id" not in toggled_keys
        assert "status" in toggled_keys
        assert "aws_price" in toggled_keys

    def test_provider_grouping_wires_all_non_lockable(self):
        from orb.ui.components.column_picker import column_picker

        vc = MagicMock()
        vc.contains = MagicMock(return_value=False)
        on_toggle = MagicMock()
        column_picker(self._cols(), vc, on_toggle, provider_column_keys={"aws_price"})
        toggled_keys = [c.args[0] for c in on_toggle.call_args_list]
        assert "status" in toggled_keys
        assert "aws_price" in toggled_keys

    def test_provider_label_derived_from_key_prefix(self):
        from orb.ui.components.column_picker import column_picker

        vc = MagicMock()
        vc.contains = MagicMock(return_value=True)
        rx.text.reset_mock()
        column_picker(
            self._cols(),
            MagicMock(contains=MagicMock(return_value=True)),
            MagicMock(),
            provider_column_keys={"aws_price"},
        )
        # The group heading is derived from the "aws" prefix -> "[AWS]".
        labels = [
            a.args[0] for a in rx.text.call_args_list if a.args and isinstance(a.args[0], str)
        ]
        assert "[AWS]" in labels

    def test_provider_label_generic_when_no_underscore(self):
        from orb.ui.components.column_picker import column_picker
        from orb.ui.components.list_grid_view import ColumnDef

        cols = [ColumnDef("region", "Region")]
        rx.text.reset_mock()
        column_picker(
            cols,
            MagicMock(contains=MagicMock(return_value=True)),
            MagicMock(),
            provider_column_keys={"region"},
        )
        labels = [
            a.args[0] for a in rx.text.call_args_list if a.args and isinstance(a.args[0], str)
        ]
        assert "[Provider]" in labels

    def test_flat_mode_when_no_provider_keys(self):
        from orb.ui.components.column_picker import column_picker

        vc = MagicMock()
        vc.contains = MagicMock(return_value=True)
        on_toggle = MagicMock()
        out = column_picker(self._cols(), vc, on_toggle, provider_column_keys=None)
        assert out is not None
        toggled_keys = [c.args[0] for c in on_toggle.call_args_list]
        assert set(toggled_keys) == {"status", "aws_price"}


# ===========================================================================
# view_prefs.py — LocalStorage default factories
# ===========================================================================


class TestViewPrefs:
    def test_view_mode_var_default_and_key(self):
        from orb.ui.components.view_prefs import view_mode_var

        v = view_mode_var("templates")
        assert str(v) == "list"
        assert v.storage_name == "orb-templates-view-mode"

    def test_view_mode_var_custom_default(self):
        from orb.ui.components.view_prefs import view_mode_var

        v = view_mode_var("machines", default="grid")
        assert str(v) == "grid"
        assert v.storage_name == "orb-machines-view-mode"

    def test_visible_columns_var_joins_keys(self):
        from orb.ui.components.view_prefs import visible_columns_var

        v = visible_columns_var("requests", ["id", "status", "created_at"])
        assert str(v) == "id,status,created_at"
        assert v.storage_name == "orb-requests-visible-cols"

    def test_visible_columns_var_empty_list(self):
        from orb.ui.components.view_prefs import visible_columns_var

        v = visible_columns_var("t", [])
        assert str(v) == ""
        assert v.storage_name == "orb-t-visible-cols"

    def test_sort_state_vars_returns_pair(self):
        from orb.ui.components.view_prefs import sort_state_vars

        sk, sd = sort_state_vars("templates", "name", "desc")
        assert str(sk) == "name"
        assert sk.storage_name == "orb-templates-sort-key"
        assert str(sd) == "desc"
        assert sd.storage_name == "orb-templates-sort-dir"

    def test_sort_state_vars_defaults(self):
        from orb.ui.components.view_prefs import sort_state_vars

        sk, sd = sort_state_vars("machines")
        assert str(sk) == ""
        assert str(sd) == "asc"


# ===========================================================================
# virtualized_list.py — JS builders, threshold constants, state setters
# ===========================================================================


class TestVirtualizedListJs:
    def test_scroll_top_js(self):
        from orb.ui.components.virtualized_list import _scroll_top_js

        assert _scroll_top_js("my-list") == "document.getElementById('my-list')?.scrollTop ?? 0"

    def test_near_bottom_js_default_threshold(self):
        from orb.ui.components.virtualized_list import _near_bottom_js

        js = _near_bottom_js("my-list")
        assert "getElementById('my-list')" in js
        assert "scrollHeight-200" in js

    def test_near_bottom_js_custom_threshold(self):
        from orb.ui.components.virtualized_list import _near_bottom_js

        js = _near_bottom_js("cid", threshold_px=350)
        assert "scrollHeight-350" in js


class TestVirtualizedListState:
    def _state(self):
        from orb.ui.components.virtualized_list import VirtualizedListState

        s = VirtualizedListState.__new__(VirtualizedListState)
        s.scroll_positions = {}
        s.near_bottom = {}
        return s

    def test_set_scroll_top_coerces_to_float(self):
        s = self._state()
        s.set_scroll_top("list-a", "42")
        assert s.scroll_positions == {"list-a": 42.0}
        assert isinstance(s.scroll_positions["list-a"], float)

    def test_set_scroll_top_preserves_other_keys(self):
        s = self._state()
        s.set_scroll_top("a", 1)
        s.set_scroll_top("b", 2)
        assert s.scroll_positions == {"a": 1.0, "b": 2.0}

    def test_set_near_bottom(self):
        s = self._state()
        s.set_near_bottom("a", True)
        s.set_near_bottom("b", False)
        assert s.near_bottom == {"a": True, "b": False}


class TestVirtualizedListComponent:
    def test_small_static_list_skips_scroll_tracking(self):
        from orb.ui.components.virtualized_list import virtualized_list

        # A tiny static list takes the fast path: the outer rx.box carries the
        # container id but NO on_scroll wiring (no scroll-state tracking).
        with patch.object(rx, "box", MagicMock(name="box")) as box:
            virtualized_list(
                [{"id": 1}, {"id": 2}],
                render_item=lambda i: i,
                container_id="small",
            )
        kwargs = box.call_args.kwargs
        assert kwargs["id"] == "small"
        assert "on_scroll" not in kwargs

    def test_threshold_constant_boundary_stays_on_fast_path(self):
        # A list exactly at the threshold is still "small" (<=), so it takes
        # the fast path with no on_scroll wiring; one over crosses to "large".
        from orb.ui.components.virtualized_list import (
            _SMALL_LIST_THRESHOLD,
            virtualized_list,
        )

        at_threshold = [{"i": n} for n in range(_SMALL_LIST_THRESHOLD)]
        with patch.object(rx, "box", MagicMock(name="box")) as box:
            virtualized_list(at_threshold, render_item=lambda i: i, container_id="edge")
        assert "on_scroll" not in box.call_args.kwargs

        one_over = [{"i": n} for n in range(_SMALL_LIST_THRESHOLD + 1)]
        with (
            patch.object(rx, "box", MagicMock(name="box")) as box,
            patch.object(rx, "call_script", MagicMock(return_value="CS")),
        ):
            virtualized_list(one_over, render_item=lambda i: i, container_id="over")
        assert "on_scroll" in box.call_args.kwargs

    def test_large_list_without_load_more_wires_only_scroll_tracker(self):
        from orb.ui.components.virtualized_list import (
            _SMALL_LIST_THRESHOLD,
            _scroll_top_js,
            virtualized_list,
        )

        big = [{"i": n} for n in range(_SMALL_LIST_THRESHOLD + 5)]
        with (
            patch.object(rx, "box", MagicMock(name="box")) as box,
            patch.object(rx, "call_script", MagicMock(return_value="CS")) as call_script,
        ):
            # No on_load_more -> exactly one scroll event (the scrollTop
            # tracker); the near-bottom / load-more scripts are NOT wired.
            virtualized_list(big, render_item=lambda i: i, container_id="big")
        scroll_events = box.call_args.kwargs["on_scroll"]
        assert len(scroll_events) == 1
        assert call_script.call_count == 1
        assert call_script.call_args.args[0] == _scroll_top_js("big")

    def test_large_list_with_load_more_wires_near_bottom_and_load_more(self):
        from orb.ui.components.virtualized_list import (
            _SMALL_LIST_THRESHOLD,
            VirtualizedListState,
            _near_bottom_js,
            _scroll_top_js,
            virtualized_list,
        )

        big = [{"i": n} for n in range(_SMALL_LIST_THRESHOLD + 5)]
        on_load_more = MagicMock(name="on_load_more")
        # near_bottom is a plain dict under the stub; make it subscriptable so
        # the reactive index (``near_bottom[container_id]``) resolves.
        nb_var = MagicMock(name="near_bottom_var")
        with (
            patch.object(rx, "box", MagicMock(name="box")) as box,
            patch.object(rx, "call_script", MagicMock(return_value="CS")) as call_script,
            patch.object(rx, "cond", MagicMock(name="cond")),
            patch.object(rx, "noop", MagicMock(name="noop")),
            patch.object(
                VirtualizedListState, "near_bottom", MagicMock(__getitem__=lambda _s, _k: nb_var)
            ),
        ):
            virtualized_list(
                big,
                render_item=lambda i: i,
                container_id="big",
                on_load_more=on_load_more,
            )
        # scrollTop tracker + near-bottom writer + load-more cond = 3 events.
        scroll_events = box.call_args.kwargs["on_scroll"]
        assert len(scroll_events) == 3
        script_js = [c.args[0] for c in call_script.call_args_list]
        assert _scroll_top_js("big") in script_js
        assert _near_bottom_js("big") in script_js


# ===========================================================================
# layout.py — nav config
# ===========================================================================


class TestLayoutNavConfig:
    def test_nav_items_cover_expected_routes(self):
        from orb.ui.components.layout import NAV_ITEMS

        routes = {href for _, href, _ in NAV_ITEMS}
        assert routes == {"/", "/templates", "/requests", "/machines", "/config"}

    def test_nav_items_are_triples(self):
        from orb.ui.components.layout import NAV_ITEMS

        for item in NAV_ITEMS:
            assert len(item) == 3
            label, href, icon = item
            assert isinstance(label, str) and label
            assert href.startswith("/")
            assert isinstance(icon, str) and icon

    def test_header_height_is_valid_css_length(self):
        import re

        from orb.ui.components.layout import HEADER_HEIGHT

        # Not a snapshot of the exact literal: assert the contract that the
        # header height is a positive CSS length usable as a row height.
        assert isinstance(HEADER_HEIGHT, str)
        m = re.fullmatch(r"(\d+(?:\.\d+)?)(rem|px|em|vh)", HEADER_HEIGHT)
        assert m is not None, f"not a CSS length: {HEADER_HEIGHT!r}"
        assert float(m.group(1)) > 0

    def test_brand_blue_is_valid_hex_color(self):
        import re

        from orb.ui.components import layout

        # Contract: the brand accent is a #rrggbb hex color, not a specific hue.
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", layout._BRAND_BLUE) is not None


class TestLayoutPage:
    def test_page_default_handlers(self):
        from orb.ui.components import layout
        from orb.ui.state import AppState

        captured = {}
        real_box = rx.box

        def _capture_box(*args, **kwargs):
            if "on_mount" in kwargs:
                captured["handlers"] = kwargs["on_mount"]
            return real_box(*args, **kwargs)

        # With no on_mount the page wires exactly the two default handlers.
        with (
            patch.object(layout, "sidebar", return_value="SIDEBAR"),
            patch.object(layout, "topbar", return_value="TOPBAR"),
            patch.object(rx, "box", side_effect=_capture_box),
        ):
            layout.page("Title")

        assert captured["handlers"] == [
            AppState.poll_health,
            AppState.load_provider_schemas,
        ]

    def test_page_appends_single_on_mount_handler(self):
        from orb.ui.components import layout

        captured = {}
        real_box = rx.box

        def _capture_box(*args, **kwargs):
            if "on_mount" in kwargs:
                captured["handlers"] = kwargs["on_mount"]
            return real_box(*args, **kwargs)

        with (
            patch.object(layout, "sidebar", return_value="S"),
            patch.object(layout, "topbar", return_value="T"),
            patch.object(rx, "box", side_effect=_capture_box),
        ):
            layout.page("Title", on_mount="CUSTOM")

        assert "CUSTOM" in captured["handlers"]
        assert len(captured["handlers"]) == 3  # 2 defaults + 1 custom

    def test_page_extends_on_mount_handler_list(self):
        from orb.ui.components import layout

        captured = {}
        real_box = rx.box

        def _capture_box(*args, **kwargs):
            if "on_mount" in kwargs:
                captured["handlers"] = kwargs["on_mount"]
            return real_box(*args, **kwargs)

        with (
            patch.object(layout, "sidebar", return_value="S"),
            patch.object(layout, "topbar", return_value="T"),
            patch.object(rx, "box", side_effect=_capture_box),
        ):
            layout.page("Title", on_mount=["H1", "H2"])

        assert "H1" in captured["handlers"]
        assert "H2" in captured["handlers"]
        assert len(captured["handlers"]) == 4  # 2 defaults + 2 custom
