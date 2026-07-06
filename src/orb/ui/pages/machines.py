"""Machines list page — full-featured table with filters, bulk select, detail drawer."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import reflex as rx

from .. import api
from ..components.column_picker import column_picker
from ..components.empty_state import empty_state
from ..components.error_callout import error_callout
from ..components.layout import page
from ..components.list_grid_view import ColumnDef, list_grid_view
from ..components.list_page_shell import list_page_shell
from ..components.machine_drawer import machine_drawer
from ..components.refresh_control import refresh_control
from ..components.request_modal import request_modal, request_success_banner
from ..components.status_badge import machine_status_badge
from ..components.view_toggle import view_toggle

# ---------------------------------------------------------------------------
# Helpers (pure Python — run at state-side, not in templates)
# ---------------------------------------------------------------------------

_EMPTY_MACHINE: dict[str, Any] = {
    "machine_id": "",
    "machine_name": "",
    "status": "",
    "instance_type": "",
    "private_ip": "",
    "public_ip": None,
    "result": "",
    "launch_time": None,
    "message": "",
    "provider_api": None,
    "provider_name": None,
    "provider_type": None,
    "resource_id": None,
    "request_id": None,
    "return_request_id": None,
    "cloud_host_id": None,
    "price_type": None,
    "private_dns_name": None,
    "public_dns_name": None,
    "metadata": None,
    "health_checks": None,
    "template_id": None,
    "image_id": None,
    "subnet_id": None,
    "security_group_ids": [],
    "status_reason": None,
    "termination_time": None,
    "tags": None,
    "provider_data": {},
    "version": 0,
    # surfaced from provider_data
    "region": None,
    "availability_zone": None,
    "vcpus": None,
    # derived
    "uptime_human": "—",
}


def _fmt_unix_ts(ts: int | str | None) -> str:
    """Format a unix-seconds int (or ISO string) into a human-readable local datetime."""
    if ts is None:
        return "—"
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            # Distinguish seconds vs milliseconds — same heuristic as the React PoC
            ms = ts if ts >= 1e12 else ts * 1000
            dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)


def _fmt_uptime(uptime_seconds: int | None, launch_time: Any) -> str:
    """Format uptime as a human-readable string like '3h 42m' or '5d 12h'.

    Prefers ``uptime_seconds`` from the API response when available.
    Falls back to computing from ``launch_time`` and now().
    """
    seconds: int | None = None
    if uptime_seconds is not None:
        try:
            seconds = int(uptime_seconds)
        except (ValueError, TypeError):
            seconds = None

    if seconds is None and launch_time is not None:
        try:
            if isinstance(launch_time, str):
                dt = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            else:
                ms = launch_time if launch_time >= 1e12 else launch_time * 1000
                dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            seconds = max(0, int((datetime.now(tz=timezone.utc) - dt).total_seconds()))
        except Exception:
            seconds = None

    if seconds is None:
        return "—"

    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _truncate_id(row: Any) -> rx.Component:
    """Truncate a long ID string (e.g. AMI IDs) to the first 12 chars."""
    val = row["image_id"]
    return rx.text(
        rx.cond(val, val, "—"),  # type: ignore[arg-type]
        size="1",
        font_family="monospace",
        color=rx.color("gray", 11),
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_VISIBLE_COLUMNS = 5

# ---------------------------------------------------------------------------
# Column formatters (receive the pre-formatted row dict as a Var)
# ---------------------------------------------------------------------------


def _id_code_machine(row: Any) -> rx.Component:
    return rx.button(
        rx.code(row["machine_id"], size="1"),  # type: ignore[index]
        on_click=MachinesState.open_drawer(row),
        variant="ghost",
        size="1",
        cursor="pointer",
    )


def _id_code_request(row: Any) -> rx.Component:
    return rx.cond(
        row["request_id"],
        rx.code(row["request_id"], size="1"),  # type: ignore[index]
        rx.text("—", size="1", color=rx.color("gray", 11)),
    )


def _uptime_text(row: Any) -> rx.Component:
    return rx.text(row["uptime_human"], size="2", color=rx.color("gray", 11))  # type: ignore[index]


def _machine_status_cell(row: Any) -> rx.Component:
    return machine_status_badge(row["status"])  # type: ignore[index]


def _checkbox_formatter_machines(row: Any) -> rx.Component:
    mid = row["machine_id"]
    return rx.checkbox(
        checked=MachinesState.selected_ids.contains(mid),
        on_change=lambda _: MachinesState.toggle_select(mid),
        size="2",
        aria_label=rx.Var.create("Select machine ") + mid.to(str),
    )


def _bool_badge_m(key: str):
    """Return a formatter that renders a bool field as yes/no badge."""

    def _fmt(row: Any) -> rx.Component:
        return rx.cond(
            row[key],
            rx.badge("yes", variant="soft", color_scheme="green", size="1"),
            rx.badge("no", variant="soft", color_scheme="gray", size="1"),
        )

    return _fmt


def _json_truncate_m(key: str):
    """Return a formatter that renders a dict/JSON field as truncated code."""

    def _fmt(row: Any) -> rx.Component:
        return rx.cond(
            row[key] != "",
            rx.code(
                row[key],
                size="1",
                white_space="nowrap",
                overflow="hidden",
                text_overflow="ellipsis",
                max_width="12rem",
                display="inline-block",
            ),
            rx.text("—", size="1", color=rx.color("gray", 9)),
        )

    return _fmt


def _list_count_m(key: str):
    """Return a formatter that shows a list field as its pre-formatted count string."""

    def _fmt(row: Any) -> rx.Component:
        return rx.cond(
            row[key] != "",
            rx.text(row[key], size="1", color=rx.color("gray", 11)),
            rx.text("—", size="1", color=rx.color("gray", 9)),
        )

    return _fmt


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

MACHINE_COLUMNS: list[ColumnDef] = [
    # --- Locked columns ---
    ColumnDef(
        "machine_id",
        "ID",
        default_visible=True,
        lockable=True,
        formatter=_id_code_machine,
    ),
    ColumnDef(
        "status",
        "Status",
        default_visible=True,
        lockable=True,
        formatter=_machine_status_cell,
    ),
    # --- Default visible (5) ---
    ColumnDef("instance_type", "Instance", default_visible=True, sortable=True),
    ColumnDef("private_ip", "Private IP", default_visible=True),
    ColumnDef(
        "uptime_human",
        "Uptime",
        default_visible=True,
        sortable=True,
        formatter=_uptime_text,
    ),
    ColumnDef(
        "request_id",
        "Request",
        default_visible=True,
        formatter=_id_code_request,
    ),
    ColumnDef("availability_zone", "AZ", default_visible=True),
    # --- All remaining DTO fields (default_visible=False) ---
    ColumnDef("machine_name", "Name", default_visible=False),
    ColumnDef("public_ip", "Public IP", default_visible=False),
    ColumnDef("private_dns_name", "Private DNS", default_visible=False),
    ColumnDef("public_dns_name", "Public DNS", default_visible=False),
    ColumnDef("region", "Region", default_visible=False),
    ColumnDef("price_type", "Pricing", default_visible=False),
    ColumnDef(
        "image_id",
        "Image",
        default_visible=False,
        formatter=_truncate_id,
    ),
    ColumnDef("template_id", "Template", default_visible=False),
    ColumnDef("return_request_id", "Return Req", default_visible=False),
    ColumnDef("result", "Result", default_visible=False),
    ColumnDef("status_reason", "Status Reason", default_visible=False),
    ColumnDef("message", "Message", default_visible=False),
    ColumnDef("vcpus", "vCPUs", default_visible=False, align="end"),
    ColumnDef("cloud_host_id", "Cloud Host", default_visible=False),
    ColumnDef("resource_id", "Resource ID", default_visible=False),
    ColumnDef("provider_name", "Provider", default_visible=False),
    ColumnDef("provider_api", "Provider API", default_visible=False),
    ColumnDef("provider_type", "Provider Type", default_visible=False),
    ColumnDef("subnet_id", "Subnet", default_visible=False),
    ColumnDef(
        "security_group_ids",
        "Sec Groups",
        default_visible=False,
        formatter=_list_count_m("security_group_ids"),
    ),
    ColumnDef("tags", "Tags", default_visible=False, formatter=_json_truncate_m("tags")),
    ColumnDef(
        "health_checks",
        "Health",
        default_visible=False,
        formatter=_json_truncate_m("health_checks"),
    ),
    ColumnDef(
        "metadata", "Metadata", default_visible=False, formatter=_json_truncate_m("metadata")
    ),
    ColumnDef(
        "provider_data",
        "Provider Data",
        default_visible=False,
        formatter=_json_truncate_m("provider_data"),
    ),
    ColumnDef("version", "Version", default_visible=False),
    ColumnDef("uptime_seconds", "Uptime (s)", default_visible=False, sortable=True, align="end"),
    ColumnDef("launch_time_fmt", "Launched", default_visible=False, sortable=True),
    ColumnDef("termination_time_fmt", "Terminated", default_visible=False, sortable=True),
]


def _select_all_header_machines() -> rx.Component:
    """Tri-state 'select all visible' checkbox for the machine table header.

    Reflects ``all_filtered_selected`` (True when every row in the current
    filtered view is selected); clicking toggles between select-all and
    deselect-all-visible. Standard data-table UX.
    """
    return rx.checkbox(
        checked=MachinesState.all_filtered_selected,
        on_change=lambda _: MachinesState.toggle_select_all,
        size="2",
        aria_label="Select all visible machines",
    )


# The checkbox select-all column — lockable, always first
_SELECT_COL = ColumnDef(
    "_select",
    "",
    default_visible=True,
    lockable=True,
    formatter=_checkbox_formatter_machines,
    header_renderer=_select_all_header_machines,
    width="2.5rem",
)

ALL_MACHINE_COLUMNS = [_SELECT_COL] + MACHINE_COLUMNS

_MACHINE_VISIBLE_DEFAULT = (
    ",machine_id,status,instance_type,private_ip,uptime_human,request_id,availability_zone,"
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class MachinesState(rx.State):
    """All client-side state for the Machines page."""

    # Raw data from API
    machines: list[dict[str, Any]] = []

    # Loading / error
    loading: bool = False
    error: str = ""

    # Filters
    status_filter: str = "all"
    search_text: str = ""

    # Selection
    selected_ids: list[str] = []  # list instead of set — Reflex serialises to JSON

    # Bulk confirm dialog (selected machines)
    confirm_return_open: bool = False

    # Bulk confirm dialog (all returnable machines on the page)
    confirm_return_all_open: bool = False
    # Loading flag for the "Return All" toolbar action — separate from
    # ``loading`` (selected-return) so spinners don't bleed across actions.
    bulk_returning: bool = False

    # Last bulk-return result — drives the success banner with a link to
    # the newly-created return request. Cleared on next bulk action.
    last_return_request_id: str = ""
    last_return_count: int = 0

    # Detail drawer
    drawer_open: bool = False
    selected_machine: dict[str, Any] = _EMPTY_MACHINE

    # Drawer-scoped provider sync — kept separate from page ``loading``
    # so the toolbar spinner does not block while a single-row sync runs.
    syncing_drawer: bool = False
    last_sync_time: str = ""
    sync_error: str = ""

    # Single-flight guard for the background polling loop
    _poll_started: bool = False

    # View preferences (persisted in localStorage)
    view_mode: str = rx.LocalStorage("list", name="orb-machines-view-mode")
    visible_columns: str = rx.LocalStorage(
        _MACHINE_VISIBLE_DEFAULT, name="orb-machines-visible-columns"
    )
    sort_key: str = rx.LocalStorage("", name="orb-machines-sort-key")
    sort_dir: str = rx.LocalStorage("asc", name="orb-machines-sort-dir")

    # Auto-refresh preferences (persisted in localStorage)
    auto_refresh_enabled: str = rx.LocalStorage("false", name="orb-machines-auto-refresh-enabled")
    auto_refresh_interval: str = rx.LocalStorage("10", name="orb-machines-auto-refresh-interval")

    # Drawer live-poll toggle (default off — machine state changes less often)
    live_poll_enabled: str = rx.LocalStorage("false", name="orb-machine-drawer-live")

    # Pagination
    next_cursor: str = ""
    api_total_count: int = 0
    loading_more: bool = False
    page_size: int = 200

    # -----------------------------------------------------------------------
    # Computed vars
    # -----------------------------------------------------------------------

    @rx.var
    def filtered_machines(self) -> list[dict[str, Any]]:
        result = self.machines
        q = self.search_text.lower().strip()
        if q:
            result = [
                m
                for m in result
                if q in (m.get("machine_id") or "").lower()
                or q in (m.get("instance_type") or "").lower()
                or q in (m.get("name") or "").lower()
            ]
        if self.status_filter != "all":
            result = [m for m in result if m.get("status") == self.status_filter]
        return result

    @rx.var
    def total_count(self) -> int:
        """Backend total-match count (or local count when not yet set by the API)."""
        return self.api_total_count if self.api_total_count > 0 else len(self.machines)

    @rx.var
    def filtered_count(self) -> int:
        return len(self.filtered_machines)

    @rx.var
    def loaded_count(self) -> int:
        """Number of rows currently held in memory (across all fetched pages)."""
        return len(self.machines)

    @rx.var
    def selected_count(self) -> int:
        return len(self.selected_ids)

    @rx.var
    def has_selection(self) -> bool:
        return len(self.selected_ids) > 0

    @rx.var
    def visible_returnable_count(self) -> int:
        """Count of currently-visible machines that can be returned.

        Returnable = status in {pending, running, stopped} (i.e. not
        terminated / shutting-down / failed). Drives whether the
        "Return All" toolbar button is rendered.
        """
        returnable = {"pending", "running", "stopped"}
        return sum(
            1 for m in self.filtered_machines if (m.get("status") or "").lower() in returnable
        )

    @rx.var
    def visible_returnable_ids(self) -> list[str]:
        """IDs of currently-visible returnable machines (used by Return All)."""
        returnable = {"pending", "running", "stopped"}
        return [
            str(m.get("machine_id") or "")
            for m in self.filtered_machines
            if (m.get("status") or "").lower() in returnable and m.get("machine_id")
        ]

    @rx.var
    def all_filtered_selected(self) -> bool:
        if not self.filtered_machines:
            return False
        filtered_ids = {m.get("machine_id", "") for m in self.filtered_machines}
        return filtered_ids.issubset(set(self.selected_ids))

    @rx.var
    def machine_rows(self) -> list[dict[str, Any]]:
        """Pre-formatted rows for list/grid rendering.
        All dict/list fields are pre-serialised to strings so Reflex
        column formatters receive typed scalars at compile time.
        """
        rows: list[dict[str, Any]] = []
        for m in self.filtered_machines:
            uptime_seconds = m.get("uptime_seconds")
            launch_time = m.get("launch_time")
            termination_time = m.get("termination_time")

            def _json_str(v: Any, limit: int = 80) -> str:
                if not v:
                    return ""
                try:
                    s = json.dumps(v, default=str)
                    return (s[:limit] + "…") if len(s) > limit else s
                except Exception:
                    return str(v)[:limit]

            def _list_str(v: Any) -> str:
                if not v or not isinstance(v, list):
                    return ""
                return f"{len(v)} items"

            rows.append(
                {
                    # --- Core identity ---
                    "machine_id": m.get("machine_id") or "",
                    "machine_name": m.get("name") or "",
                    "status": m.get("status") or "",
                    "instance_type": m.get("instance_type") or "",
                    # --- Network ---
                    "private_ip": m.get("private_ip") or "",
                    "public_ip": m.get("public_ip") or "",
                    "private_dns_name": m.get("private_dns_name") or "",
                    "public_dns_name": m.get("public_dns_name") or "",
                    "availability_zone": m.get("availability_zone") or "",
                    "region": m.get("region") or "",
                    "subnet_id": m.get("subnet_id") or "",
                    "security_group_ids": _list_str(m.get("security_group_ids")),
                    # --- Lifecycle ---
                    "result": m.get("result") or "",
                    "status_reason": m.get("status_reason") or "",
                    "message": m.get("message") or "",
                    "uptime_human": _fmt_uptime(uptime_seconds, launch_time),
                    "uptime_seconds": int(uptime_seconds) if uptime_seconds is not None else 0,
                    "launch_time_fmt": _fmt_unix_ts(launch_time),
                    "termination_time_fmt": _fmt_unix_ts(termination_time),
                    # --- Request linkage ---
                    "request_id": m.get("request_id") or "",
                    "return_request_id": m.get("return_request_id") or "",
                    "template_id": m.get("template_id") or "",
                    # --- Compute ---
                    "vcpus": int(m.get("vcpus") or 0),
                    "image_id": m.get("image_id") or "",
                    "price_type": m.get("price_type") or "",
                    # --- Cloud provider ---
                    "cloud_host_id": m.get("cloud_host_id") or "",
                    "resource_id": m.get("resource_id") or "",
                    "provider_api": m.get("provider_api") or "",
                    "provider_name": m.get("provider_name") or "",
                    "provider_type": m.get("provider_type") or "",
                    # --- Nested dicts (truncated JSON strings) ---
                    "tags": _json_str(m.get("tags")),
                    "health_checks": _json_str(m.get("health_checks")),
                    "metadata": _json_str(m.get("metadata")),
                    "provider_data": _json_str(m.get("provider_data")),
                    # --- Versioning ---
                    "version": str(m.get("version") or ""),
                    # --- Sort helpers (numeric) ---
                    "_uptime_seconds": int(uptime_seconds) if uptime_seconds is not None else 0,
                    "_launch_ts": int(launch_time) if isinstance(launch_time, int) else 0,
                    # Pass through the raw dict so card/drawer can open it
                    "raw": m,
                }
            )
        return rows

    @rx.var
    def sorted_rows(self) -> list[dict[str, Any]]:
        """Sorted version of machine_rows."""
        rows = list(self.machine_rows)
        sk = self.sort_key
        sd = self.sort_dir
        if not sk:
            return rows

        # Use numeric sort fields for uptime and launch_time
        if sk == "uptime_human":
            sort_field = "_uptime_seconds"
        elif sk == "launch_time_fmt":
            sort_field = "_launch_ts"
        else:
            sort_field = sk

        def _key(r: dict[str, Any]) -> Any:
            v = r.get(sort_field, "")
            if v is None:
                return ""
            return v

        return sorted(rows, key=_key, reverse=(sd == "desc"))

    # -----------------------------------------------------------------------
    # Drawer computed vars — formatted strings pre-computed in Python so
    # templates stay simple (no conversion logic inside rx.cond / rx.match).
    # -----------------------------------------------------------------------

    @rx.var
    def selected_machine_launch_fmt(self) -> str:
        return _fmt_unix_ts(self.selected_machine.get("launch_time"))

    @rx.var
    def selected_machine_term_fmt(self) -> str:
        return _fmt_unix_ts(self.selected_machine.get("termination_time"))

    @rx.var
    def selected_machine_tags_text(self) -> str:
        tags = self.selected_machine.get("tags")
        if not tags:
            return "{}"
        try:
            return json.dumps(tags, indent=2)
        except Exception:
            return str(tags)

    @rx.var
    def selected_machine_health_text(self) -> str:
        hc = self.selected_machine.get("health_checks")
        if hc is None:
            return "null"
        try:
            return json.dumps(hc, indent=2)
        except Exception:
            return str(hc)

    @rx.var
    def selected_machine_provider_data_text(self) -> str:
        pd = self.selected_machine.get("provider_data")
        if not pd:
            return "{}"
        try:
            return json.dumps(pd, indent=2)
        except Exception:
            return str(pd)

    @rx.var
    def selected_machine_sg_text(self) -> str:
        sgs = self.selected_machine.get("security_group_ids") or []
        return ", ".join(sgs) if sgs else "—"

    # -----------------------------------------------------------------------
    # Events — data loading
    # -----------------------------------------------------------------------

    def _normalize_visible_columns(self) -> None:
        """Ensure visible_columns uses the fenced format ,k1,k2,...,kN,."""
        vc = self.visible_columns
        if vc and not vc.startswith(","):
            keys = [k for k in vc.split(",") if k]
            self.visible_columns = "," + ",".join(keys) + "," if keys else ","

    @rx.event
    async def load(self) -> None:
        """Fetch first page of machines from the API and reset pagination."""
        self._normalize_visible_columns()
        self.loading = True
        self.error = ""
        # Reset pagination for a fresh load
        self.next_cursor = ""
        self.api_total_count = 0
        try:
            status = None if self.status_filter == "all" else self.status_filter
            res = await api.list_machines(status=status, limit=self.page_size)
            rows = res.get("machines", [])
            self.machines = rows
            self.next_cursor = res.get("next_cursor") or ""
            self.api_total_count = int(res.get("total_count") or len(rows))
            self.last_sync_time = datetime.now().strftime("%H:%M:%S")
        except Exception as e:
            self.error = f"Failed to load machines: {e}"
        finally:
            self.loading = False

    @rx.event(background=True)
    async def auto_refresh(self) -> None:
        """Poll the API on a configurable interval. Single-flight via _poll_started so
        re-mounting the page doesn't spawn additional background loops.

        Reads ``auto_refresh_enabled`` and ``auto_refresh_interval`` from state
        on each iteration so changes take effect without restart.
        """
        async with self:
            if self._poll_started:
                return
            self._poll_started = True
        try:
            while True:
                async with self:
                    enabled = self.auto_refresh_enabled == "true"
                    interval_str = self.auto_refresh_interval or "10"
                if not enabled:
                    await asyncio.sleep(5)  # poll the flag while disabled
                    continue
                try:
                    interval = max(5, int(interval_str))
                except ValueError:
                    interval = 10
                await asyncio.sleep(interval)
                async with self:
                    if not self.loading:
                        try:
                            status = None if self.status_filter == "all" else self.status_filter
                            page_size = self.page_size
                            res = await api.list_machines(status=status, limit=page_size)
                            rows = res.get("machines", [])
                            self.machines = rows
                            self.next_cursor = res.get("next_cursor") or ""
                            self.api_total_count = int(res.get("total_count") or len(rows))
                            self.last_sync_time = datetime.now().strftime("%H:%M:%S")
                        except Exception as e:
                            self.error = f"Auto-refresh failed: {e}"
        finally:
            async with self:
                self._poll_started = False

    @rx.event
    def toggle_auto_refresh(self, checked: bool) -> None:
        """Adapter: rx.checkbox passes bool; we store as 'true'/'false' string."""
        self.auto_refresh_enabled = "true" if checked else "false"

    @rx.event
    def set_auto_refresh_enabled(self, value: str) -> None:
        self.auto_refresh_enabled = value

    @rx.event
    def set_auto_refresh_interval(self, value: str) -> None:
        self.auto_refresh_interval = value

    @rx.event
    def toggle_live_poll(self, checked: bool) -> None:
        """Toggle the machine drawer live-poll on/off. Stored as 'true'/'false' for LocalStorage."""
        self.live_poll_enabled = "true" if checked else "false"

    @rx.event(background=True)
    async def load_more(self) -> None:
        """Append the next page of machines to the in-memory list.

        State-locking pattern: we read ``loading_more`` and ``next_cursor``
        inside a short ``async with self:`` block, then release the lock
        before making the API call so other event handlers remain responsive.
        The ``finally`` block reclaims the lock to clear ``loading_more``
        even if the API call raises.
        """
        async with self:
            if self.loading_more or not self.next_cursor:
                return
            self.loading_more = True
            cursor = self.next_cursor
            status = None if self.status_filter == "all" else self.status_filter
            page_size = self.page_size
        try:
            res = await api.list_machines(status=status, cursor=cursor, limit=page_size)
            new_rows = res.get("machines", [])
            async with self:
                self.machines = list(self.machines) + new_rows
                self.next_cursor = res.get("next_cursor") or ""
                self.api_total_count = int(res.get("total_count") or self.api_total_count)
        except Exception as e:
            async with self:
                self.error = f"Failed to load more machines: {e}"
        finally:
            async with self:
                self.loading_more = False

    @rx.event
    async def set_page_size(self, value: str) -> None:
        """Change the page size and trigger a fresh first-page load."""
        try:
            self.page_size = int(value)
        except (ValueError, TypeError):
            return
        yield MachinesState.load

    @rx.event(background=True)
    async def poll_drawer_machine(self) -> None:
        """Poll the open machine every 3s until terminal or drawer closes.

        Snapshots the machine_id at start; aborts when the drawer closes
        or the selected machine changes. Respects ``live_poll_enabled``:
        when paused the loop sleeps briefly without hitting the API.
        """
        async with self:
            mid = str((self.selected_machine or {}).get("machine_id") or "")
            if not mid or not self.drawer_open:
                return
        while True:
            async with self:
                if not self.drawer_open:
                    return
                current = str((self.selected_machine or {}).get("machine_id") or "")
                if current != mid:
                    return
                paused = self.live_poll_enabled != "true"
            if paused:
                await asyncio.sleep(2)
                continue
            try:
                full = await api.get_machine(mid)
                if isinstance(full, dict):
                    if (
                        "machines" in full
                        and isinstance(full["machines"], list)
                        and full["machines"]
                    ):
                        full = full["machines"][0]
                    async with self:
                        # Don't trample a concurrent Sync click. ``sync_drawer_machine``
                        # sets ``syncing_drawer=True`` for the duration of its API
                        # call; the poll's pure-read response would otherwise race
                        # the sync's provider-side fetch and overwrite the fresher
                        # data with the stale local view.
                        if (
                            self.drawer_open
                            and str((self.selected_machine or {}).get("machine_id") or "") == mid
                            and not self.syncing_drawer
                        ):
                            self.selected_machine = {**_EMPTY_MACHINE, **full}
            except Exception:
                # API error during background poll — keep polling; drawer will retry on next tick
                pass
            async with self:
                status = str((self.selected_machine or {}).get("status") or "").lower()
            if status in ("terminated", "failed"):
                return
            await asyncio.sleep(3)

    # -----------------------------------------------------------------------
    # Events — filters
    # -----------------------------------------------------------------------

    @rx.event
    async def set_status_filter(self, value: str) -> None:
        self.status_filter = value
        self.next_cursor = ""
        self.api_total_count = 0
        await self.load()  # type: ignore[misc]

    @rx.event
    def set_search_text(self, value: str) -> None:
        self.search_text = value

    # -----------------------------------------------------------------------
    # Events — selection
    # -----------------------------------------------------------------------

    @rx.event
    def toggle_select(self, machine_id: str) -> None:
        ids = list(self.selected_ids)
        if machine_id in ids:
            ids.remove(machine_id)
        else:
            ids.append(machine_id)
        self.selected_ids = ids

    @rx.event
    def toggle_select_all(self) -> None:
        filtered_ids = [m.get("machine_id", "") for m in self.filtered_machines]
        if self.all_filtered_selected:
            # Deselect all filtered
            self.selected_ids = [sid for sid in self.selected_ids if sid not in filtered_ids]
        else:
            # Select all filtered (merge, avoid duplicates)
            current = set(self.selected_ids)
            current.update(filtered_ids)
            self.selected_ids = list(current)

    @rx.event
    def clear_selection(self) -> None:
        self.selected_ids = []

    # -----------------------------------------------------------------------
    # Events — view preferences
    # -----------------------------------------------------------------------

    @rx.event
    def set_view_mode(self, mode: str) -> None:
        self.view_mode = mode

    @rx.event
    def toggle_column(self, key: str, checked: bool) -> None:
        # visible_columns is stored as ",k1,k2,...,kN," (fenced format).
        keys = [k for k in self.visible_columns.split(",") if k]
        locked_keys = {c.key for c in ALL_MACHINE_COLUMNS_WITH_ACTIONS if c.lockable}
        visible_non_locked = [k for k in keys if k not in locked_keys]
        if checked and key not in keys:
            if len(visible_non_locked) >= MAX_VISIBLE_COLUMNS:
                # Soft cap: silently drop the oldest non-locked column to make room
                keys.remove(visible_non_locked[0])
            keys.append(key)
        elif not checked and key in keys:
            keys.remove(key)
        self.visible_columns = "," + ",".join(keys) + "," if keys else ","

    @rx.event
    def set_sort(self, key: str) -> None:
        if self.sort_key == key:
            self.sort_dir = "desc" if self.sort_dir == "asc" else "asc"
        else:
            self.sort_key = key
            self.sort_dir = "asc"

    # -----------------------------------------------------------------------
    # Events — bulk confirm dialog
    # -----------------------------------------------------------------------

    @rx.event
    def open_confirm_return(self) -> None:
        self.confirm_return_open = True

    @rx.event
    def close_confirm_return(self) -> None:
        self.confirm_return_open = False

    @rx.event(background=True)
    async def return_selected(self) -> None:
        """Return selected machines to the pool.

        background=True so the Return Selected button grays out (loading=loading)
        immediately when clicked instead of waiting for the state lock.
        """
        async with self:
            if self.loading:
                return  # re-entrancy guard
            ids = list(self.selected_ids)
            if not ids:
                return
            self.loading = True
            # Reset banner state from any previous return so the new one
            # replaces it cleanly.
            self.last_return_request_id = ""
            self.last_return_count = 0
            self.error = ""
            status_filter = self.status_filter  # snapshot before releasing lock
        try:
            result = await api.return_machines({"machine_ids": ids})
            # Capture the new return-request id so the page can show a
            # success banner linking to its detail (operator can drill in
            # to watch termination progress).
            rid = str((result or {}).get("request_id") or "")
            async with self:
                page_size = self.page_size
            res = await api.list_machines(
                status=None if status_filter == "all" else status_filter,
                limit=page_size,
            )
            async with self:
                if rid:
                    self.last_return_request_id = rid
                    self.last_return_count = len(ids)
                self.selected_ids = []  # uncheck everything
                self.confirm_return_open = False
                rows = res.get("machines", [])
                self.machines = rows
                self.next_cursor = res.get("next_cursor") or ""
                self.api_total_count = int(res.get("total_count") or len(rows))
                self.loading = False
        except Exception as e:
            async with self:
                self.error = f"Return failed: {e}"
                self.loading = False

    @rx.event
    def dismiss_return_banner(self) -> None:
        """Hide the bulk-return success banner."""
        self.last_return_request_id = ""
        self.last_return_count = 0

    # -----------------------------------------------------------------------
    # Events — return all (toolbar action across all visible returnable rows)
    # -----------------------------------------------------------------------

    @rx.event
    def open_confirm_return_all(self) -> None:
        self.confirm_return_all_open = True

    @rx.event
    def dismiss_return_all(self) -> None:
        self.confirm_return_all_open = False

    @rx.event(background=True)
    async def return_all_visible(self) -> None:
        """Return EVERY visible-and-returnable machine to the pool.

        Operates on ``visible_returnable_ids`` so filters apply — operators
        can scope the action via the status pills / search box.
        background=True so the toolbar button grays out immediately.
        """
        async with self:
            if self.bulk_returning:
                return  # re-entrancy guard
            ids = list(self.visible_returnable_ids)
            if not ids:
                self.confirm_return_all_open = False
                return
            self.bulk_returning = True
            self.last_return_request_id = ""
            self.last_return_count = 0
            self.error = ""
            status_filter = self.status_filter
        try:
            result = await api.return_machines({"machine_ids": ids})
            rid = str((result or {}).get("request_id") or "")
            async with self:
                page_size = self.page_size
            res = await api.list_machines(
                status=None if status_filter == "all" else status_filter,
                limit=page_size,
            )
            async with self:
                if rid:
                    self.last_return_request_id = rid
                    self.last_return_count = len(ids)
                self.selected_ids = []
                self.confirm_return_all_open = False
                rows = res.get("machines", [])
                self.machines = rows
                self.next_cursor = res.get("next_cursor") or ""
                self.api_total_count = int(res.get("total_count") or len(rows))
                self.bulk_returning = False
        except Exception as e:
            async with self:
                self.error = f"Return failed: {e}"
                self.bulk_returning = False
                self.confirm_return_all_open = False

    # -----------------------------------------------------------------------
    # Events — per-row return (confirm inline via alert_dialog per row)
    # -----------------------------------------------------------------------

    @rx.event(background=True)
    async def return_one(self, machine_id: str) -> None:
        """Return a single machine. background=True so the row button grays out immediately."""
        async with self:
            if self.loading:
                return  # re-entrancy guard
            self.loading = True
            self.error = ""
            status_filter = self.status_filter  # snapshot before releasing lock
        try:
            await api.return_machines({"machine_ids": [machine_id]})
            async with self:
                page_size = self.page_size
            res = await api.list_machines(
                status=None if status_filter == "all" else status_filter,
                limit=page_size,
            )
            async with self:
                rows = res.get("machines", [])
                self.machines = rows
                self.next_cursor = res.get("next_cursor") or ""
                self.api_total_count = int(res.get("total_count") or len(rows))
                self.loading = False
        except Exception as e:
            async with self:
                self.error = f"Return failed: {e}"
                self.loading = False

    # -----------------------------------------------------------------------
    # Events — drawer
    # -----------------------------------------------------------------------

    @rx.event(background=True)
    async def open_drawer(self, machine: dict[str, Any]):
        """Open the machine detail drawer and fetch full machine data.

        background=True so the state lock is not held during the API call.
        State mutations are wrapped in ``async with self:``; the API call
        itself runs outside the lock so other event handlers remain
        responsive while the detail fetch is in flight.
        """
        # Merge with empty template so all keys always exist.
        # If called from a row dict (machine_rows), use the embedded raw dict.
        raw = machine.get("raw") or machine
        async with self:
            self.selected_machine = {**_EMPTY_MACHINE, **raw}
            self.drawer_open = True
            self.sync_error = ""
            machine_id = self.selected_machine.get("machine_id", "")
            if not machine_id:
                return
            self.syncing_drawer = True
        try:
            # API call outside lock — other handlers can run concurrently.
            full = await api.get_machine(machine_id)
            if isinstance(full, dict):
                if "machines" in full and isinstance(full["machines"], list) and full["machines"]:
                    full = full["machines"][0]
            async with self:
                self.selected_machine = {**_EMPTY_MACHINE, **full}
        except Exception as exc:
            async with self:
                self.sync_error = f"Failed to load full machine details: {exc}"
        finally:
            async with self:
                self.syncing_drawer = False
        yield MachinesState.poll_drawer_machine

    @rx.event
    def close_drawer(self) -> None:
        self.drawer_open = False

    @rx.event
    def set_drawer_open(self, value: bool) -> None:
        self.drawer_open = value

    @rx.event
    async def sync_drawer_machine(self) -> None:
        """Refresh the open machine from the provider.

        Hits GET /api/v1/machines/{id}/status, which performs one
        DescribeInstances and persists any change. The page-level list
        is not reloaded — only the drawer view and the corresponding
        in-memory row.
        """
        machine_id = self.selected_machine.get("machine_id", "")
        if not machine_id:
            return
        self.syncing_drawer = True
        self.sync_error = ""
        try:
            payload = await api.sync_machine(machine_id)
            if payload.get("synced") is False and payload.get("sync_error"):
                self.sync_error = str(payload["sync_error"])
            # Drop pagination/wrapper keys, keep the machine fields.
            refreshed = {**_EMPTY_MACHINE, **payload}
            refreshed.pop("synced", None)
            refreshed.pop("sync_error", None)
            self.selected_machine = refreshed
            # Mirror the update into the in-memory page list so the
            # row behind the drawer reflects the new status.
            updated_list = []
            for row in self.machines:
                if row.get("machine_id") == machine_id:
                    merged_row = {**row, **refreshed}
                    updated_list.append(merged_row)
                else:
                    updated_list.append(row)
            self.machines = updated_list
            self.last_sync_time = datetime.now().strftime("%H:%M:%S")
        except Exception as exc:
            self.sync_error = f"Sync failed: {exc}"
        finally:
            self.syncing_drawer = False

    @rx.event
    async def sync_row(self, machine_id: str) -> None:
        """Sync a single row from the provider without opening the drawer.

        Calls the same /machines/{id}/status endpoint the drawer uses
        and patches the matching row in the in-memory page list. Errors
        surface in ``sync_error``.
        """
        if not machine_id:
            return
        self.sync_error = ""
        try:
            payload = await api.sync_machine(machine_id)
            if payload.get("synced") is False and payload.get("sync_error"):
                self.sync_error = str(payload["sync_error"])
            refreshed = {**_EMPTY_MACHINE, **payload}
            refreshed.pop("synced", None)
            refreshed.pop("sync_error", None)
            self.machines = [
                {**r, **refreshed} if r.get("machine_id") == machine_id else r
                for r in self.machines
            ]
            if self.selected_machine.get("machine_id") == machine_id:
                self.selected_machine = refreshed
            self.last_sync_time = datetime.now().strftime("%H:%M:%S")
        except Exception as exc:
            self.sync_error = f"Sync failed: {exc}"

    @rx.event
    async def return_drawer_machine(self) -> None:
        machine_id = self.selected_machine.get("machine_id", "")
        if not machine_id:
            return
        self.drawer_open = False
        await self.return_one(machine_id)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Sub-components
# ---------------------------------------------------------------------------

# Status filter pill options: (value, display_label)
MACHINE_STATUS_FILTERS: list[tuple[str, str]] = [
    ("all", "All"),
    ("running", "Running"),
    ("pending", "Pending"),
    ("stopped", "Stopped"),
    ("terminated", "Terminated"),
    ("shutting-down", "Shutting Down"),
]

STATUS_OPTIONS = ["all", "pending", "running", "stopped", "terminated", "shutting-down"]


def _machine_card(row: Any) -> rx.Component:
    """Card renderer for grid view mode."""
    return rx.card(
        rx.vstack(
            # Header: machine ID + status badge
            rx.hstack(
                rx.code(row["machine_id"], size="1"),
                rx.spacer(),
                machine_status_badge(row["status"]),
                align="center",
                width="100%",
            ),
            rx.divider(),
            # Instance type + IP
            rx.hstack(
                rx.icon("server", size=14, color=rx.color("gray", 10)),
                rx.text(row["instance_type"], size="2"),
                spacing="2",
                align="center",
            ),
            rx.hstack(
                rx.icon("network", size=14, color=rx.color("gray", 10)),
                rx.text(row["private_ip"], size="2", font_family="monospace"),
                spacing="2",
                align="center",
            ),
            # Uptime + AZ
            rx.hstack(
                rx.icon("clock", size=14, color=rx.color("gray", 10)),
                rx.text(row["uptime_human"], size="2", color=rx.color("gray", 11)),
                spacing="2",
                align="center",
            ),
            rx.cond(
                row["availability_zone"] != "",
                rx.hstack(
                    rx.icon("map-pin", size=14, color=rx.color("gray", 10)),
                    rx.text(row["availability_zone"], size="2", color=rx.color("gray", 11)),
                    spacing="2",
                    align="center",
                ),
                rx.fragment(),
            ),
            # Image ID (truncated)
            rx.cond(
                row["image_id"] != "",
                rx.text(
                    row["image_id"],
                    size="1",
                    color=rx.color("gray", 10),
                    font_family="monospace",
                    no_wrap=True,
                    overflow="hidden",
                    text_overflow="ellipsis",
                ),
                rx.fragment(),
            ),
            # Request ID
            rx.cond(
                row["request_id"] != "",
                rx.hstack(
                    rx.text("Req:", size="1", color=rx.color("gray", 10)),
                    rx.code(row["request_id"], size="1"),
                    spacing="1",
                    align="center",
                ),
                rx.fragment(),
            ),
            rx.divider(),
            # Footer actions
            rx.hstack(
                rx.icon_button(
                    rx.icon("eye", size=14),
                    size="1",
                    variant="ghost",
                    on_click=MachinesState.open_drawer(row),
                    title="View detail",
                ),
                rx.icon_button(
                    rx.icon("cloud-download", size=14),
                    size="1",
                    variant="ghost",
                    color_scheme="blue",
                    on_click=MachinesState.sync_row(row["machine_id"]),
                    title="Sync from provider",
                ),
                rx.cond(
                    (row["status"] == "pending")
                    | (row["status"] == "running")
                    | (row["status"] == "stopped"),
                    rx.icon_button(
                        rx.icon("log-out", size=14),
                        size="1",
                        variant="ghost",
                        color_scheme="red",
                        on_click=MachinesState.return_one(row["machine_id"]),
                        title="Return machine",
                    ),
                    rx.fragment(),
                ),
                spacing="2",
                align="center",
            ),
            spacing="2",
            width="100%",
        ),
        cursor="pointer",
        on_click=MachinesState.open_drawer(row),
        width="100%",
    )


def _row_actions(row: Any) -> rx.Component:
    """Icon-only row actions rendered via formatter — View / Sync / Return."""
    mid = row["machine_id"]
    return rx.hstack(
        rx.icon_button(
            rx.icon("eye", size=14),
            size="1",
            variant="ghost",
            on_click=MachinesState.open_drawer(row),
            title="View detail",
        ),
        rx.icon_button(
            rx.icon("cloud-download", size=14),
            size="1",
            variant="ghost",
            color_scheme="blue",
            on_click=MachinesState.sync_row(mid),
            title="Sync from provider",
        ),
        rx.cond(
            row["status"] == "running",
            rx.alert_dialog.root(
                rx.alert_dialog.trigger(
                    rx.icon_button(
                        rx.icon("log-out", size=14),
                        size="1",
                        variant="ghost",
                        color_scheme="red",
                        title="Return machine",
                    ),
                ),
                rx.alert_dialog.content(
                    rx.alert_dialog.title("Return Machine"),
                    rx.alert_dialog.description(
                        rx.vstack(
                            rx.text(
                                "This will return the machine to the pool. "
                                "This action cannot be undone."
                            ),
                            rx.code(mid, size="1"),
                            spacing="2",
                        ),
                    ),
                    rx.hstack(
                        rx.alert_dialog.cancel(
                            rx.button("Cancel", variant="soft", color_scheme="gray"),
                        ),
                        rx.alert_dialog.action(
                            rx.button(
                                "Return Machine",
                                color_scheme="red",
                                on_click=MachinesState.return_one(mid),
                            ),
                        ),
                        spacing="3",
                        justify="end",
                        margin_top="1rem",
                    ),
                ),
            ),
            rx.fragment(),
        ),
        spacing="2",
        align="center",
        justify="end",
    )


# Actions column def (lockable so always visible, not in picker)
_ACTIONS_COL = ColumnDef(
    "_actions",
    "Actions",
    default_visible=True,
    lockable=True,
    formatter=_row_actions,
    align="end",
)

ALL_MACHINE_COLUMNS_WITH_ACTIONS = ALL_MACHINE_COLUMNS + [_ACTIONS_COL]


def _empty_state() -> rx.Component:
    return rx.cond(
        (MachinesState.search_text != "") | (MachinesState.status_filter != "all"),
        empty_state(
            icon="server-off",
            title="No machines found",
            description="Try adjusting your filters.",
        ),
        empty_state(
            icon="server-off",
            title="No machines found",
            description="No machines have been allocated yet.",
        ),
    )


def _selection_actions() -> rx.Component:
    """Inline selection-action chip rendered inside the toolbar when ≥1
    machine is selected. Order matches the bulk-all action below it:
    [Return Selected (N)] (with confirm dialog).
    """
    return rx.cond(
        MachinesState.has_selection,
        rx.alert_dialog.root(
            rx.alert_dialog.trigger(
                rx.button(
                    rx.icon("log-out", size=14),
                    "Return Selected (" + MachinesState.selected_count.to_string() + ")",
                    size="2",
                    variant="soft",
                    color_scheme="red",
                    loading=MachinesState.loading,
                    title="Return checked machines to the provider",
                ),
            ),
            rx.alert_dialog.content(
                rx.alert_dialog.title(
                    "Return " + MachinesState.selected_count.to_string() + " Machine(s)"
                ),
                rx.alert_dialog.description(
                    "These machines will be returned to the pool. This action cannot be undone."
                ),
                rx.hstack(
                    rx.alert_dialog.cancel(
                        rx.button("Cancel", variant="soft", color_scheme="gray"),
                    ),
                    rx.alert_dialog.action(
                        rx.button(
                            "Return Machines",
                            color_scheme="red",
                            on_click=MachinesState.return_selected,
                            loading=MachinesState.loading,
                        ),
                    ),
                    spacing="3",
                    justify="end",
                    margin_top="1rem",
                ),
            ),
        ),
        rx.fragment(),
    )


def _filter_row() -> rx.Component:
    """Canonical filter row: status pills + search input + spacer + refresh_control.

    Layout:
        [Status pills] [Search input] <spacer> [refresh_control]
    """
    return rx.hstack(
        # Status filter pills (replaces rx.select dropdown)
        rx.hstack(
            *[
                rx.button(
                    label,
                    size="2",
                    variant=rx.cond(MachinesState.status_filter == value, "solid", "soft"),
                    color_scheme=rx.cond(MachinesState.status_filter == value, "blue", "gray"),
                    radius="full",
                    on_click=MachinesState.set_status_filter(value),
                    role="tab",
                    aria_selected=rx.cond(MachinesState.status_filter == value, "true", "false"),
                )
                for value, label in MACHINE_STATUS_FILTERS
            ],
            role="tablist",
            spacing="2",
            flex_wrap="wrap",
            align="center",
        ),
        # Search input
        rx.input(
            placeholder="Search…",
            value=MachinesState.search_text,
            on_change=MachinesState.set_search_text,
            width="240px",
        ),
        rx.spacer(),
        # Auto-refresh control
        refresh_control(
            enabled=MachinesState.auto_refresh_enabled,
            interval=MachinesState.auto_refresh_interval,
            on_toggle=MachinesState.toggle_auto_refresh,
            on_set_interval=MachinesState.set_auto_refresh_interval,
            on_manual_refresh=MachinesState.load,
            last_refresh_text=MachinesState.last_sync_time,
            loading=MachinesState.loading,
        ),
        spacing="2",
        flex_wrap="wrap",
        align="center",
        margin_bottom="1rem",
        width="100%",
    )


def _toolbar() -> rx.Component:
    """Toolbar: count badge + spacer + bulk actions + view toggle + column picker.

    Machines page has no primary Create action (machines are created via Requests).
    "Return All" mirrors the Requests page "Cancel All Active" pattern — only
    visible when there is at least one returnable machine on the page.
    """
    return rx.hstack(
        # Count badge (left): "Showing X of Y machines"
        rx.hstack(
            rx.text("Showing", size="2", color=rx.color("gray", 11)),
            rx.badge(
                MachinesState.loaded_count.to_string()
                + " of "
                + MachinesState.total_count.to_string(),
                variant="soft",
                color_scheme="gray",
                size="1",
            ),
            rx.text("machines", size="2", color=rx.color("gray", 11)),
            # Page-size selector
            rx.select(
                ["50", "100", "200", "500"],
                value=MachinesState.page_size.to_string(),
                on_change=MachinesState.set_page_size,
                size="1",
                width="80px",
            ),
            rx.text("per page", size="2", color=rx.color("gray", 11)),
            spacing="2",
            align="center",
        ),
        rx.spacer(),
        # Right-side action buttons
        rx.flex(
            # Inline selection-action chip (Return Selected + clear)
            _selection_actions(),
            rx.cond(
                MachinesState.visible_returnable_count > 0,
                rx.button(
                    rx.icon("log-out", size=14),
                    "Return All (" + MachinesState.visible_returnable_count.to_string() + ")",
                    size="2",
                    variant="soft",
                    color_scheme="red",
                    loading=MachinesState.bulk_returning,
                    on_click=MachinesState.open_confirm_return_all,
                    title="Return every visible returnable machine to the provider",
                ),
                rx.fragment(),
            ),
            view_toggle(
                mode=MachinesState.view_mode,
                on_change=MachinesState.set_view_mode,
            ),
            column_picker(
                columns=MACHINE_COLUMNS,
                visible_columns=MachinesState.visible_columns,
                on_toggle=MachinesState.toggle_column,
            ),
            flex_wrap="wrap",
            row_gap="0.5rem",
            gap="0.5rem",
            align="center",
        ),
        align="center",
        margin_bottom="1rem",
        width="100%",
        flex_wrap="wrap",
        row_gap="0.5rem",
    )


def _confirm_return_all_dialog() -> rx.Component:
    """Confirmation dialog for the toolbar "Return All" action."""
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Return All Visible Machines"),
            rx.alert_dialog.description(
                rx.vstack(
                    rx.text(
                        "Return ",
                        rx.code(MachinesState.visible_returnable_count.to_string(), size="1"),
                        " machine(s) to the provider?",
                        as_="span",
                    ),
                    rx.text(
                        "Filters apply — only visible returnable machines "
                        "(pending/running/stopped) will be terminated. This "
                        "cannot be undone.",
                        size="2",
                        color=rx.color("gray", 11),
                    ),
                    spacing="2",
                    align="start",
                )
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.button(
                        "Keep",
                        variant="soft",
                        color_scheme="gray",
                        on_click=MachinesState.dismiss_return_all,
                    ),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Return All",
                        color_scheme="red",
                        loading=MachinesState.bulk_returning,
                        on_click=MachinesState.return_all_visible,
                    ),
                ),
                spacing="3",
                justify="end",
                margin_top="1.5rem",
            ),
        ),
        open=MachinesState.confirm_return_all_open,
    )


def _loading_skeleton() -> rx.Component:
    """Animated loading placeholders while data fetches."""
    return rx.vstack(
        *[rx.skeleton(height="3rem", width="100%", border_radius="0.375rem") for _ in range(5)],
        spacing="2",
        width="100%",
    )


def _select_all_header(_unused: Any) -> rx.Component:
    """Select-all checkbox rendered in the header.

    Note: list_grid_view renders headers from ColumnDef.title.  The
    select-all checkbox in the header is handled separately here by
    overriding the column header for ``_select``.
    """
    return rx.checkbox(
        checked=MachinesState.all_filtered_selected,
        on_change=lambda _: MachinesState.toggle_select_all(),
        size="2",
        aria_label="Select all visible machines",
    )


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------


def _return_banner() -> rx.Component:
    """Bulk-return success banner linking to the newly created return request."""
    return rx.cond(
        MachinesState.last_return_request_id != "",
        # Banner wrapped in an hstack so the dismiss X stays inline on
        # the right; Radix's ``rx.callout.root`` lays children out as
        # grid (icon | text), which forces ``rx.spacer`` + the X to
        # wrap onto a second row.
        rx.hstack(
            rx.callout.root(
                rx.callout.icon(rx.icon("check-circle-2")),
                rx.callout.text(
                    "Return request created for ",
                    rx.text.strong(MachinesState.last_return_count.to_string()),
                    " machine(s). ",
                    rx.link(
                        rx.code(MachinesState.last_return_request_id, size="1"),
                        href="/requests?id=" + MachinesState.last_return_request_id,
                        underline="hover",
                        margin_left="0.25rem",
                    ),
                ),
                color_scheme="green",
                width="100%",
            ),
            rx.icon_button(
                rx.icon("x", size=14),
                on_click=MachinesState.dismiss_return_banner,
                variant="ghost",
                size="1",
                color_scheme="gray",
                aria_label="Dismiss",
            ),
            spacing="2",
            align="center",
            width="100%",
            margin_bottom="1rem",
        ),
        rx.fragment(),
    )


def _load_more_button() -> rx.Component:
    """Load-more button shown when a next-page cursor is available."""
    return rx.cond(
        MachinesState.next_cursor != "",
        rx.center(
            rx.button(
                rx.cond(
                    MachinesState.loading_more,
                    rx.spinner(size="2"),
                    rx.icon("chevrons-down", size=16),
                ),
                rx.cond(
                    MachinesState.loading_more,
                    "Loading…",
                    "Load more",
                ),
                on_click=MachinesState.load_more,
                disabled=MachinesState.loading_more,
                variant="soft",
                color_scheme="gray",
                size="2",
            ),
            width="100%",
            padding_top="0.75rem",
        ),
        rx.fragment(),
    )


def machines_page() -> rx.Component:
    """Machines page component — entry point registered in orb_ui.py."""
    return page(
        "Machines",
        list_page_shell(
            error_banner=rx.cond(
                MachinesState.error != "",
                error_callout(MachinesState.error, retry=MachinesState.load),
                rx.fragment(),
            ),
            banners=[_return_banner(), request_success_banner()],
            filter_row=_filter_row(),
            toolbar=_toolbar(),
            grid=list_grid_view(
                rows=MachinesState.sorted_rows,
                columns=ALL_MACHINE_COLUMNS_WITH_ACTIONS,
                view_mode=MachinesState.view_mode,
                visible_columns=MachinesState.visible_columns,
                sort_key=MachinesState.sort_key,
                sort_dir=MachinesState.sort_dir,
                card_renderer=_machine_card,
                on_row_click=None,  # row click handled per-cell
                on_sort=MachinesState.set_sort,
            ),
            load_more=_load_more_button(),
            empty=_empty_state(),
            is_loading=MachinesState.loading & (MachinesState.loaded_count == 0),
            is_empty=MachinesState.filtered_count == 0,
            loading_skeleton=_loading_skeleton(),
            dialogs=[
                machine_drawer(MachinesState),
                request_modal(),
                _confirm_return_all_dialog(),
            ],
        ),
        on_mount=[MachinesState.load, MachinesState.auto_refresh],
    )
