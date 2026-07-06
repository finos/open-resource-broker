"""Requests list page.

Replicates the React PoC's Requests.jsx feature set in Reflex Python:

- Filter tabs: All / In Progress / Completed / Failed / Cancelled / Timeout / Returns
- Table with: Request ID, Status (color-coded pill), Type, Template,
  Requested, Fulfilled, Created, Actions (cancel)
- Progress bar per row (fulfilled / requested ratio)
- Click row → RequestDrawer with full detail
- Cancel button per row (non-terminal only) with confirmation dialog
- Tab-filtered request counts
- Empty state when no rows in current filter
- Error callout on API failure
- Loading state on initial load
- Auto-refresh every 10 s (on_mount background task)
"""

from __future__ import annotations

import asyncio
import datetime
import json
from collections import Counter
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
from ..components.machine_quick_view import MachineQuickViewState
from ..components.refresh_control import refresh_control
from ..components.request_drawer import request_drawer
from ..components.request_modal import RequestModalState, request_modal, request_success_banner
from ..components.status_badge import request_status_badge
from ..components.view_toggle import view_toggle

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_VISIBLE_COLUMNS = 5

# ---------------------------------------------------------------------------
# UI-side wire-contract mirrors
# These constants reflect the lowercase snake_case values the REST API
# returns in JSON.  They are NOT coupled to any Python enum — they mirror
# the observed wire payload.  If the backend adds new terminal/failure
# statuses, update these sets AND (preferred) ask the backend to expose an
# `is_terminal` flag in the response payload so UI logic stays trivial.
# ---------------------------------------------------------------------------

# Wire-format literals emitted by ``RequestStatus.value``. The canonical
# source is ``orb.domain.request.request_types.RequestStatus`` — note
# ``COMPLETED = "complete"`` (without the ``-ed``).
_TERMINAL_STATUSES: set[str] = {"complete", "failed", "cancelled", "timeout", "partial"}
_FAILURE_LIKE_STATUSES: set[str] = {"failed", "partial", "timeout"}


def _empty_request_skeleton() -> dict[str, Any]:
    """UI mirror of REST snake_case response shape.

    NOT a coupling to RequestDTO — keys are derived from the observed wire
    payload (snake_case JSON).  Reflex needs this initial shape so state Vars
    resolve at compile time; an empty dict would leave subscripts unresolvable.
    """
    return {
        "request_id": "",
        "status": "",
        "request_type": "",
        "template_id": "",
        "requested_count": 0,
        "successful_count": 0,
        "returned_count": 0,
        "failed_count": 0,
        "created_at": "",
        "last_status_check": "",
        "completed_at": "",
        "started_at": "",
        "message": "",
        "machines": [],
        "machine_ids": [],
        "error_details": {},
        "metadata": {},
        "provider_api": "",
        "provider_name": "",
        "provider_type": "",
        "desired_capacity": 0,
        "success_rate": 0,
        "provider_data": {},
        "version": "",
        "resource_id": "",
        "resource_ids": [],
        "duration": 0,
        "first_status_check": "",
        "launch_template_id": "",
        "launch_template_version": "",
        # Weighted-capacity fleet fulfilment fields (EC2Fleet, ASG mixed,
        # SpotFleet). Emitted at the top level by the response formatter
        # for fleet-like providers. Absent / 0 for single-instance flows.
        "fulfilled_units": 0,
        "target_units": 0,
        "running_count": 0,
        "pending_count": 0,
    }


def _is_terminal_status(status: str) -> bool:
    """Return True if *status* is a terminal wire-format value."""
    return (status or "").lower() in _TERMINAL_STATUSES


def _is_failure_like(status: str) -> bool:
    """Return True if *status* represents a failure-like terminal outcome."""
    return (status or "").lower() in _FAILURE_LIKE_STATUSES


# ---------------------------------------------------------------------------
# Column formatters (receive the pre-formatted row Var)
# ---------------------------------------------------------------------------


def _id_code(row: Any) -> rx.Component:
    return rx.button(
        rx.code(row["request_id"], size="1"),
        on_click=RequestsState.open_drawer(row["raw"]),
        variant="ghost",
        size="1",
        cursor="pointer",
    )


def _request_status_cell(row: Any) -> rx.Component:
    return request_status_badge(row["status"])


def _request_type_cell(row: Any) -> rx.Component:
    """Acquire vs return badge with directional icon + colour.

    Acquire = blue + download-arrow (machines flowing in to the pool).
    Return  = amber + upload-arrow  (machines flowing out / back).
    Visually distinct so the eye can scan a mixed list and instantly tell
    which rows are provisions and which are tear-downs.
    """
    is_return = row["request_type"] == "return"
    return rx.hstack(
        rx.cond(
            is_return,
            rx.icon("log-out", size=12, color=rx.color("amber", 11)),
            rx.icon("log-in", size=12, color=rx.color("blue", 11)),
        ),
        rx.badge(
            row["request_type"],
            color_scheme=rx.cond(is_return, "amber", "blue"),
            variant="soft",
            size="1",
        ),
        spacing="1",
        align="center",
    )


def _progress_bar_formatter(row: Any) -> rx.Component:
    """Formatter wrapper — receives the row Var and extracts required fields.

    When the request used weighted capacity (EC2Fleet, ASG mixed, SpotFleet),
    ``is_weighted`` is True and we display ``fulfilled_units / target_units``
    with a small "units" label so the operator sees the same numbers as the
    drawer, instead of the misleadingly low instance count.
    """
    bar_color = rx.match(
        row["status"],
        ("failed", rx.color("red", 9)),
        ("complete", rx.color("green", 9)),
        ("completed", rx.color("green", 9)),
        rx.color("amber", 9),
    )
    return rx.vstack(
        rx.box(
            rx.box(
                height="0.375rem",
                border_radius="full",
                background=bar_color,
                width=row["progress_pct"].to_string() + "%",
                transition="width 0.3s ease",
            ),
            height="0.375rem",
            background=rx.color("gray", 4),
            border_radius="full",
            overflow="hidden",
            width="100%",
            role="progressbar",
            aria_valuenow=row["progress_pct"],
            aria_valuemin=0,
            aria_valuemax=100,
            aria_label="Request fulfillment progress",
        ),
        rx.cond(
            row["is_weighted"],
            rx.hstack(
                rx.text(row["fulfilled_units"], size="1", color=rx.color("gray", 10)),
                rx.text("/", size="1", color=rx.color("gray", 10)),
                rx.text(row["target_units"], size="1", color=rx.color("gray", 10)),
                rx.text("units", size="1", color=rx.color("gray", 9)),
                spacing="1",
            ),
            rx.hstack(
                rx.text(row["successful_count"], size="1", color=rx.color("gray", 10)),
                rx.text("/", size="1", color=rx.color("gray", 10)),
                rx.text(row["requested_count"], size="1", color=rx.color("gray", 10)),
                spacing="1",
            ),
        ),
        spacing="1",
        align="center",
        min_width="80px",
    )


def _duration_text(row: Any) -> rx.Component:
    return rx.text(row["duration_fmt"], size="2", color=rx.color("gray", 11))


def _truncate_text(row: Any) -> rx.Component:
    return rx.text(
        row["message"],
        size="1",
        color=rx.color("gray", 10),
        no_wrap=True,
        overflow="hidden",
        text_overflow="ellipsis",
        max_width="200px",
    )


def _checkbox_formatter_requests(row: Any) -> rx.Component:
    rid = row["request_id"]
    return rx.checkbox(
        checked=RequestsState.selected_ids.contains(rid),
        on_change=lambda _: RequestsState.toggle_select(rid),
        size="2",
        aria_label=rx.Var.create("Select request ") + rid.to(str),
    )


def _bool_badge_r(key: str):
    """Return a formatter that renders a bool field as yes/no badge."""

    def _fmt(row: Any) -> rx.Component:
        return rx.cond(
            row[key],
            rx.badge("yes", variant="soft", color_scheme="green", size="1"),
            rx.badge("no", variant="soft", color_scheme="gray", size="1"),
        )

    return _fmt


def _json_truncate_r(key: str):
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


def _list_count_r(key: str):
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


def _select_all_header_requests() -> rx.Component:
    """Tri-state 'select all visible' checkbox for the request table header.

    Reflects ``all_visible_selected`` (True when every row in the current
    filtered view is selected); clicking toggles between select-all and
    deselect-all-visible — standard data-table UX.
    """
    return rx.checkbox(
        checked=RequestsState.all_visible_selected,
        on_change=lambda _: RequestsState.toggle_select_all,
        size="2",
        aria_label="Select all visible requests",
    )


_SELECT_COL_REQUESTS = ColumnDef(
    "_select",
    "",
    default_visible=True,
    lockable=True,
    formatter=_checkbox_formatter_requests,
    header_renderer=_select_all_header_requests,
    width="2.5rem",
)

REQUEST_COLUMNS: list[ColumnDef] = [
    # --- Locked columns ---
    ColumnDef(
        "request_id",
        "ID",
        default_visible=True,
        lockable=True,
        formatter=_id_code,
    ),
    ColumnDef(
        "status",
        "Status",
        default_visible=True,
        lockable=True,
        formatter=_request_status_cell,
    ),
    # --- Default visible (5) ---
    ColumnDef(
        "request_type",
        "Type",
        default_visible=True,
        formatter=_request_type_cell,
    ),
    ColumnDef("template_id", "Template", default_visible=True),
    ColumnDef(
        "progress_pct",
        "Progress",
        default_visible=True,
        formatter=_progress_bar_formatter,
    ),
    ColumnDef("created_at", "Created", default_visible=True, sortable=True),
    ColumnDef("failed_count", "Failed", default_visible=True, align="end"),
    # --- All remaining DTO fields (default_visible=False) ---
    ColumnDef("requested_count", "Requested", default_visible=False, align="end"),
    ColumnDef("successful_count", "Fulfilled", default_visible=False, align="end"),
    ColumnDef("returned_count", "Returned", default_visible=False, align="end"),
    ColumnDef("desired_capacity", "Desired Cap", default_visible=False, align="end"),
    ColumnDef("started_at", "Started", default_visible=False, sortable=True),
    ColumnDef("completed_at", "Completed", default_visible=False, sortable=True),
    ColumnDef("first_status_check", "First Check", default_visible=False),
    ColumnDef("last_status_check", "Last Check", default_visible=False),
    ColumnDef(
        "duration_fmt",
        "Duration",
        default_visible=True,
        formatter=_duration_text,
    ),
    ColumnDef("success_rate_fmt", "Success %", default_visible=False),
    ColumnDef("message", "Message", default_visible=False, formatter=_truncate_text),
    ColumnDef("provider_api", "Provider API", default_visible=False),
    ColumnDef("provider_name", "Provider", default_visible=False),
    ColumnDef("provider_type", "Prov Type", default_visible=False),
    ColumnDef(
        "provider_data",
        "Prov Data",
        default_visible=False,
        formatter=_json_truncate_r("provider_data"),
    ),
    ColumnDef("resource_id", "Resource ID", default_visible=False),
    ColumnDef(
        "resource_ids",
        "Resource IDs",
        default_visible=False,
        formatter=_list_count_r("resource_ids"),
    ),
    ColumnDef(
        "machine_ids", "Machine IDs", default_visible=False, formatter=_list_count_r("machine_ids")
    ),
    ColumnDef("launch_template_id", "Launch Tmpl", default_visible=False),
    ColumnDef("launch_template_version", "Tmpl Ver", default_visible=False),
    ColumnDef(
        "metadata", "Metadata", default_visible=False, formatter=_json_truncate_r("metadata")
    ),
    ColumnDef(
        "error_details",
        "Error Details",
        default_visible=False,
        formatter=_json_truncate_r("error_details"),
    ),
    ColumnDef("version", "Version", default_visible=False),
    ColumnDef(
        "is_terminal", "Terminal", default_visible=False, formatter=_bool_badge_r("is_terminal")
    ),
    ColumnDef(
        "is_failure_like",
        "Failure",
        default_visible=False,
        formatter=_bool_badge_r("is_failure_like"),
    ),
    ColumnDef("progress_percent", "Progress %", default_visible=False, align="end"),
]


# Actions column (lockable, always shown, not in column picker)
def _request_row_actions(row: Any) -> rx.Component:
    """Icon-only row actions: View / Sync / Cancel."""
    return rx.hstack(
        rx.icon_button(
            rx.icon("eye", size=14),
            size="1",
            variant="ghost",
            on_click=RequestsState.open_drawer(row["raw"]),
            title="View detail",
        ),
        rx.icon_button(
            rx.icon("cloud-download", size=14),
            size="1",
            variant="ghost",
            color_scheme="blue",
            loading=RequestsState.bulk_syncing,
            on_click=RequestsState.sync_row(row["request_id"]),
            title="Sync from provider",
        ),
        rx.cond(
            ~row["is_terminal"],
            rx.icon_button(
                rx.icon("x", size=14),
                size="1",
                variant="ghost",
                color_scheme="red",
                loading=RequestsState.cancelling,
                on_click=RequestsState.confirm_cancel_row(row["request_id"]),
                title="Cancel request",
            ),
            rx.fragment(),
        ),
        spacing="2",
        align="center",
        justify="end",
    )


_ACTIONS_COL_REQUESTS = ColumnDef(
    "_actions",
    "Actions",
    default_visible=True,
    lockable=True,
    formatter=_request_row_actions,
    align="end",
)

ALL_REQUEST_COLUMNS = [_SELECT_COL_REQUESTS] + REQUEST_COLUMNS + [_ACTIONS_COL_REQUESTS]

_REQUEST_VISIBLE_DEFAULT = (
    ",request_id,status,request_type,template_id,progress_pct,created_at,duration_fmt,failed_count,"
)


_TABS: list[tuple[str, str]] = [
    ("all", "All"),
    ("in_progress", "In Progress"),
    ("complete", "Completed"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
    ("timeout", "Timeout"),
    ("returns", "Returns"),
]

# Mapping from tab value → status filter passed to the API.
# Keys match the wire literals from ``RequestStatus.value``; "all" and
# "returns" do not filter by status.
_TAB_TO_STATUS: dict[str, str | None] = {
    "all": None,
    "in_progress": "in_progress",
    "complete": "complete",
    "failed": "failed",
    "cancelled": "cancelled",
    "timeout": "timeout",
    "returns": None,
}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class RequestsState(rx.State):
    """State for the Requests page."""

    # Raw list loaded from the API — always the full response for the
    # current tab.  No client-side secondary filtering is applied.
    requests: list[dict[str, Any]] = []

    loading: bool = False
    error: str = ""

    # Active filter tab value
    tab: str = "all"

    # Search text for client-side filtering
    search_text: str = ""

    # Drawer
    drawer_open: bool = False
    # Initialise from RequestDTO so every field defined by ORB is
    # known to Reflex's compile-time schema inference. With an empty
    # dict default, ``selected_request[key]`` would render as a raw
    # Var literal because Reflex can't see the keys ahead of time.
    selected_request: dict[str, Any] = _empty_request_skeleton()

    # Drawer-scoped provider sync — kept separate from page ``loading``
    # so the toolbar spinner does not block while a single-row sync runs.
    syncing_drawer: bool = False
    last_sync_time: str = ""
    sync_error: str = ""

    # Per-row selection for batch operations. List (not set) because
    # Reflex serialises state to JSON.
    selected_ids: list[str] = []

    # Bulk sync indicator — separate from per-row ``loading`` so a long
    # batch does not block individual row drawers.
    bulk_syncing: bool = False
    bulk_sync_error: str = ""

    # Cancel confirmation dialog
    confirm_cancel_open: bool = False
    cancel_target_request_id: str = ""

    # Cancel-all confirmation dialog (terminates every non-terminal
    # request currently in the visible list, with their machines).
    confirm_cancel_all_open: bool = False

    # Cancel-selected confirmation dialog (mirrors cancel-all but scoped
    # to the current checkbox selection).
    confirm_cancel_selected_open: bool = False

    bulk_cancelling: bool = False
    bulk_cancel_error: str = ""

    # Single-request cancel double-click guard.
    # Set True at the start of do_cancel, False in its finally block.
    # Wire loading=RequestsState.cancelling to cancel buttons so they
    # show a spinner and become non-interactive during the API call.
    cancelling: bool = False

    # View preferences (persisted in localStorage)
    view_mode: str = rx.LocalStorage("list", name="orb-requests-view-mode")
    visible_columns: str = rx.LocalStorage(
        _REQUEST_VISIBLE_DEFAULT, name="orb-requests-visible-columns"
    )
    sort_key: str = rx.LocalStorage("", name="orb-requests-sort-key")
    sort_dir: str = rx.LocalStorage("asc", name="orb-requests-sort-dir")

    # Auto-refresh preferences (persisted in localStorage)
    auto_refresh_enabled: str = rx.LocalStorage("false", name="orb-requests-auto-refresh-enabled")
    auto_refresh_interval: str = rx.LocalStorage("10", name="orb-requests-auto-refresh-interval")

    # Drawer live-poll toggle (persisted in localStorage)
    live_poll_enabled: str = rx.LocalStorage("true", name="orb-request-drawer-live")

    # Last refresh timestamp (for display)
    last_refresh: str = ""

    # Pagination
    next_cursor: str = ""
    api_total_count: int = 0
    loading_more: bool = False
    page_size: int = 200

    # ---------------------------------------------------------------------------
    # Computed vars
    # ---------------------------------------------------------------------------

    @rx.var
    def filtered_count(self) -> int:
        """Number of requests currently visible."""
        return len(self.requests)

    @rx.var
    def total_requests(self) -> int:
        """Backend total-match count (or local count when not yet set by the API)."""
        return self.api_total_count if self.api_total_count > 0 else len(self.requests)

    @rx.var
    def loaded_count(self) -> int:
        """Number of rows currently held in memory (across all fetched pages)."""
        return len(self.requests)

    @rx.var
    def has_requests(self) -> bool:
        return len(self.requests) > 0

    @rx.var
    def selected_count(self) -> int:
        return len(self.selected_ids)

    @rx.var
    def has_selection(self) -> bool:
        return len(self.selected_ids) > 0

    @rx.var
    def all_visible_selected(self) -> bool:
        if not self.requests:
            return False
        visible_ids = {r.get("request_id", "") for r in self.requests}
        return visible_ids.issubset(set(self.selected_ids))

    @rx.var
    def visible_active_count(self) -> int:
        """How many non-terminal requests are currently visible.

        Drives the enabled-state of the "Cancel All" toolbar button.
        """
        return sum(
            1 for r in self.requests if (r.get("status") or "").lower() not in _TERMINAL_STATUSES
        )

    # Machines list for the drawer — typed list so foreach works.
    @rx.var
    def drawer_machines(self) -> list[dict[str, Any]]:
        r = self.selected_request or {}
        machines = r.get("machines") or []
        out: list[dict[str, Any]] = []
        for m in machines:
            out.append(
                {
                    "machine_id": m.get("machine_id") or m.get("cloud_host_id") or "—",
                    "status": m.get("status") or "unknown",
                    "instance_type": m.get("instance_type") or "—",
                    "private_ip_address": m.get("private_ip_address") or "",
                }
            )
        return out

    # Per-field typed Vars for the drawer. Mirrors the Templates pattern.
    # Returning a `dict[str, Any]` from a computed @rx.var collapses every
    # subscript to AnyVar at compile time, which breaks rx.cond,
    # .to_string(), and bitwise operators on bool members. Splitting the
    # view into discrete typed Vars makes Reflex render each field.

    @rx.var
    def selected_request_id(self) -> str:
        return str((self.selected_request or {}).get("request_id") or "")

    @rx.var
    def selected_request_status(self) -> str:
        return str((self.selected_request or {}).get("status") or "").lower()

    @rx.var
    def selected_request_type(self) -> str:
        return str((self.selected_request or {}).get("request_type") or "acquire")

    @rx.var
    def selected_request_template(self) -> str:
        return str((self.selected_request or {}).get("template_id") or "—")

    @rx.var
    def selected_request_requested_count(self) -> int:
        return int((self.selected_request or {}).get("requested_count") or 0)

    @rx.var
    def selected_request_successful_count(self) -> int:
        return int((self.selected_request or {}).get("successful_count") or 0)

    @rx.var
    def selected_request_returned_count(self) -> int:
        return int((self.selected_request or {}).get("returned_count") or 0)

    @rx.var
    def selected_request_created_at(self) -> str:
        return str((self.selected_request or {}).get("created_at") or "—")

    @rx.var
    def selected_request_last_status_check(self) -> str:
        return str((self.selected_request or {}).get("last_status_check") or "—")

    @rx.var
    def selected_request_completed_at(self) -> str:
        return str((self.selected_request or {}).get("completed_at") or "—")

    @rx.var
    def selected_request_message(self) -> str:
        return str((self.selected_request or {}).get("message") or "")

    @rx.var
    def selected_request_progress_pct(self) -> int:
        r = self.selected_request or {}
        requested = int(r.get("requested_count") or 0)
        fulfilled = int(r.get("successful_count") or 0)
        if requested <= 0:
            return 0
        return min(int((fulfilled / requested) * 100), 100)

    @rx.var
    def selected_request_fulfilled_units(self) -> int:
        """Weighted capacity units fulfilled.

        Reads top-level ``fulfilled_units`` (the canonical wire field
        emitted by the response formatter for fleet-like requests).
        Falls back to legacy ``metadata.last_fulfilment.fulfilled_units``
        only if the top-level field is absent.
        """
        r = self.selected_request or {}
        fu = r.get("fulfilled_units")
        if fu is None:
            meta = r.get("metadata") or {}
            lf = meta.get("last_fulfilment") or {}
            fu = lf.get("fulfilled_units")
        try:
            return int(fu) if fu is not None else 0
        except (TypeError, ValueError):
            return 0

    @rx.var
    def selected_request_target_units(self) -> int:
        """Weighted capacity units targeted.

        Top-level ``target_units`` first, then legacy
        ``metadata.last_fulfilment.target_units``.
        """
        r = self.selected_request or {}
        tu = r.get("target_units")
        if tu is None:
            meta = r.get("metadata") or {}
            lf = meta.get("last_fulfilment") or {}
            tu = lf.get("target_units")
        try:
            return int(tu) if tu is not None else 0
        except (TypeError, ValueError):
            return 0

    @rx.var
    def selected_request_is_weighted(self) -> bool:
        """True when the request was a capacity/units-based request.

        Backend writes ``target_units`` whenever the provider supplied
        capacity data (EC2Fleet, ASG mixed, SpotFleet). Trust that signal
        — if target_units > 0 the request uses unit semantics, regardless
        of whether fulfilled_units == successful_count.
        """
        tu = self.selected_request_target_units
        return tu > 0

    @rx.var
    def selected_request_display_units_fulfilled(self) -> int:
        """``fulfilled_units`` if backend supplied it, else successful_count.

        Used for drawer rendering when ``is_weighted`` is True but the
        provider didn't write ``fulfilled_units`` (instant fleets w/ weight=1).
        """
        return self.selected_request_fulfilled_units or self.selected_request_successful_count

    @rx.var
    def selected_request_display_units_target(self) -> int:
        """``target_units`` if supplied, else ``requested_count``."""
        return self.selected_request_target_units or self.selected_request_requested_count

    @rx.var
    def selected_request_progress_pct_weighted(self) -> int:
        """Progress as units/units when weighted, else falls back to instance ratio.

        When the request is weighted but ``fulfilled_units`` was never
        populated by the provider (instant fleets with weight=1 per
        machine), fall back to instance counts for the numerator so the
        bar still fills correctly.
        """
        if self.selected_request_is_weighted:
            tu = self.selected_request_target_units or self.selected_request_requested_count
            fu = self.selected_request_fulfilled_units or self.selected_request_successful_count
            if tu > 0:
                return min(100, int(fu / tu * 100))
        rc = self.selected_request_requested_count
        sc = self.selected_request_successful_count
        if rc > 0:
            return min(100, int(sc / rc * 100))
        return 0

    @rx.var
    def selected_request_is_terminal(self) -> bool:
        return _is_terminal_status(str((self.selected_request or {}).get("status") or ""))

    @rx.var
    def selected_request_is_return(self) -> bool:
        return str((self.selected_request or {}).get("request_type") or "acquire") == "return"

    @rx.var
    def selected_request_has_message(self) -> bool:
        return bool((self.selected_request or {}).get("message"))

    @rx.var
    def selected_request_has_machines(self) -> bool:
        return bool((self.selected_request or {}).get("machines"))

    @rx.var
    def selected_request_show_progress(self) -> bool:
        return int((self.selected_request or {}).get("requested_count") or 0) > 0

    @rx.var
    def selected_request_instance_types_breakdown(self) -> str:
        """Per-instance-type counts for the request's machines.

        Returns e.g. ``"t3.medium x 4, t3.xlarge x 2"`` for mixed fleets,
        ``"t3.medium x 1"`` for single-type, ``""`` when no machines present
        (the row should be hidden in that case).

        Counts ALL machines on the request regardless of status, including
        terminated ones on return requests so the breakdown reflects what
        was actually returned.
        """
        r = self.selected_request or {}
        machines = r.get("machines") or []
        if not machines:
            return ""
        counts: Counter = Counter((m.get("instance_type") or "").strip() for m in machines)
        # Drop empty-type buckets (machines that lack instance_type in their record).
        counts.pop("", None)
        if not counts:
            return ""
        parts = [f"{t} x {n}" for t, n in counts.most_common()]
        return ", ".join(parts)

    @rx.var
    def selected_request_has_instance_breakdown(self) -> bool:
        """True when at least one machine has an instance_type.

        Drives the drawer to show the Instance Types row only when meaningful.
        """
        return self.selected_request_instance_types_breakdown != ""

    @rx.var
    def selected_request_is_failure_like(self) -> bool:
        return _is_failure_like(str((self.selected_request or {}).get("status") or ""))

    # -----------------------------------------------------------------------
    # Timeline var — 5 fixed lifecycle events, grayed-out when timestamp
    # is absent.  Consumed by the drawer timeline strip.
    # -----------------------------------------------------------------------

    @rx.var
    def selected_request_timeline(self) -> list[dict[str, str]]:
        """Return up to 5 lifecycle events derived from timestamp fields.

        Each entry: {ts, label, color, present} where *present* is "1"/"0"
        (str because Reflex Var schema must be homogeneous str).
        Missing timestamps produce a grayed-out placeholder row.
        """
        r = self.selected_request or {}
        status = str(r.get("status") or "").lower()

        _events: list[tuple[str, str, str]] = [
            ("created_at", "Created", "blue"),
            ("started_at", "Provisioning started", "amber"),
            ("first_status_check", "First status check", "violet"),
            ("last_status_check", "Last status check", "violet"),
            ("completed_at", "Completed", "green"),
        ]

        # If the request is failure-like, override completed_at color.
        if _is_failure_like(status):
            _events = [
                ("created_at", "Created", "blue"),
                ("started_at", "Provisioning started", "amber"),
                ("first_status_check", "First status check", "violet"),
                ("last_status_check", "Last status check", "violet"),
                ("completed_at", "Failed / partial", "red"),
            ]

        rows: list[dict[str, str]] = []
        for field, label, color in _events:
            raw_ts = r.get(field) or ""
            ts_str = str(raw_ts)
            # Trim to 19 chars (YYYY-MM-DDTHH:MM:SS) for compact display.
            display_ts = ts_str[:19] if len(ts_str) > 19 else ts_str
            rows.append(
                {
                    "label": label,
                    "ts": display_ts,
                    "color": color if display_ts else "gray",
                    "present": "1" if display_ts else "0",
                }
            )
        return rows

    @rx.var
    def selected_request_metadata_str(self) -> str:
        """Caller-supplied free-form metadata. Usually empty — the API client
        rarely sets it."""
        md = (self.selected_request or {}).get("metadata") or {}
        if not md:
            return "(empty)"
        try:
            return json.dumps(md, indent=2, sort_keys=True, default=str)
        except Exception:
            return str(md)

    @rx.var
    def selected_request_provider_data_str(self) -> str:
        """Provider-side enrichment: fulfilment result, capacity diagnostics,
        spot/on-demand split, etc. Populated by the provider strategy on
        fulfilment."""
        pd = (self.selected_request or {}).get("provider_data") or {}
        if not pd:
            return "(empty)"
        try:
            return json.dumps(pd, indent=2, sort_keys=True, default=str)
        except Exception:
            return str(pd)

    @rx.var
    def selected_request_raw_json(self) -> str:
        """Full JSON of the request row as the API returned it. Mirrors the
        DB entry plus the UI-friendly derived fields the router adds."""
        req = self.selected_request or {}
        if not req:
            return "(empty)"
        try:
            return json.dumps(req, indent=2, sort_keys=True, default=str)
        except Exception:
            return str(req)

    @rx.var
    def selected_request_capacity_chart_data(self) -> list[dict]:
        """Donut chart data: fulfilled / failed / remaining capacity slices."""
        r = self.selected_request or {}
        is_weighted = self.selected_request_is_weighted
        if is_weighted:
            fulfilled = self.selected_request_fulfilled_units
            target = self.selected_request_target_units
            failed = int(r.get("failed_count") or 0)
        else:
            fulfilled = int(r.get("successful_count") or 0)
            target = int(r.get("requested_count") or 0)
            failed = int(r.get("failed_count") or 0)
        remaining = max(0, target - fulfilled - failed)
        data: list[dict] = []
        if fulfilled > 0:
            data.append({"name": "Fulfilled", "value": fulfilled, "fill": "#22c55e"})
        if failed > 0:
            data.append({"name": "Failed", "value": failed, "fill": "#ef4444"})
        if remaining > 0:
            data.append({"name": "Remaining", "value": remaining, "fill": "#e5e7eb"})
        return data

    @rx.var
    def selected_request_machine_status_data(self) -> list[dict]:
        """Bar chart data: machine counts grouped by status."""
        r = self.selected_request or {}
        machines = r.get("machines") or []
        counts: Counter = Counter((m.get("status") or "unknown").lower() for m in machines)
        palette = {
            "running": "#22c55e",
            "pending": "#f59e0b",
            "in_progress": "#3b82f6",
            "terminated": "#6b7280",
            "shutting-down": "#f59e0b",
            "failed": "#ef4444",
            "unknown": "#9ca3af",
        }
        return [
            {"status": s, "count": c, "fill": palette.get(s, "#9ca3af")}
            for s, c in counts.most_common()
        ]

    @rx.event
    def set_search_text(self, value: str) -> None:
        self.search_text = value

    @rx.var
    def request_rows(self) -> list[dict[str, Any]]:
        """Pre-formatted rows for the table — all string ops & defaults computed
        in Python so templates are free of conditional Var operations.
        All dict/list fields are pre-serialised to strings so Reflex column
        formatters receive typed scalars at compile time.
        """
        # Apply client-side search filter
        source = self.requests
        q = self.search_text.lower().strip()
        if q:
            source = [
                r
                for r in source
                if q in (r.get("request_id") or "").lower()
                or q in (r.get("template_id") or "").lower()
                or q in (r.get("status") or "").lower()
            ]

        rows: list[dict[str, Any]] = []
        for r in source:
            status = (r.get("status") or "").lower()
            requested = int(r.get("requested_count") or 0)
            fulfilled = int(r.get("successful_count") or 0)
            ratio = (fulfilled / requested) if requested > 0 else 0.0
            created = r.get("created_at") or ""
            created_short = created[:19] if len(created) > 19 else (created or "—")

            def _ts_short(v: Any) -> str:
                s = str(v or "")
                return s[:19] if len(s) > 19 else (s or "—")

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

            # Duration formatting
            dur_sec = r.get("duration")
            if dur_sec is not None:
                try:
                    dur_sec = int(dur_sec)
                    dur_min = dur_sec // 60
                    dur_rem = dur_sec % 60
                    duration_fmt = f"{dur_min}m {dur_rem}s" if dur_min > 0 else f"{dur_sec}s"
                except (ValueError, TypeError):
                    duration_fmt = "—"
            else:
                duration_fmt = "—"

            # Success rate
            sr = r.get("success_rate")
            success_rate_fmt = f"{int(sr * 100)}%" if sr is not None else "—"

            is_terminal = _is_terminal_status(status)
            is_failure = _is_failure_like(status)

            # Weighted-capacity progress (EC2Fleet, ASG mixed, SpotFleet).
            # ``fulfilled_units`` counts capacity units fulfilled; it differs
            # from ``successful_count`` (instance count) when machine_types
            # have non-unit weights.  When the two values differ we display
            # units so the list matches the drawer.
            fu = int(r.get("fulfilled_units") or 0)
            tu = int(r.get("target_units") or 0)
            is_weighted = fu > 0 and fu != fulfilled
            if is_weighted and tu > 0:
                progress_pct = min(100, int(fu / tu * 100))
            else:
                progress_pct = int(ratio * 100)

            rows.append(
                {
                    # --- Core identity ---
                    "request_id": r.get("request_id") or "",
                    "status": status,
                    "request_type": r.get("request_type") or "acquire",
                    "template_id": r.get("template_id") or "—",
                    # --- Counts ---
                    "requested_count": requested,
                    "successful_count": fulfilled,
                    "failed_count": int(r.get("failed_count") or 0),
                    "returned_count": int(r.get("returned_count") or 0),
                    "desired_capacity": int(r.get("desired_capacity") or 0),
                    # --- Weighted-capacity fulfilment ---
                    "fulfilled_units": fu,
                    "target_units": tu,
                    "is_weighted": is_weighted,
                    # --- Progress ---
                    "progress_pct": progress_pct,
                    "progress_percent": progress_pct,
                    # --- Timestamps ---
                    "created": created_short,
                    "created_at": created_short,
                    "started_at": _ts_short(r.get("started_at")),
                    "completed_at": _ts_short(r.get("completed_at")),
                    "first_status_check": _ts_short(r.get("first_status_check")),
                    "last_status_check": _ts_short(r.get("last_status_check")),
                    # --- Outcome ---
                    "duration_fmt": duration_fmt,
                    "success_rate_fmt": success_rate_fmt,
                    "message": r.get("message") or "",
                    # --- Derived booleans ---
                    "is_terminal": is_terminal,
                    "is_failure_like": is_failure,
                    # --- Provider ---
                    "provider_api": r.get("provider_api") or "",
                    "provider_name": r.get("provider_name") or "",
                    "provider_type": r.get("provider_type") or "",
                    # --- Resource identifiers ---
                    "resource_id": r.get("resource_id") or "",
                    "launch_template_id": r.get("launch_template_id") or "",
                    "launch_template_version": r.get("launch_template_version") or "",
                    # --- List fields (pre-formatted) ---
                    "resource_ids": _list_str(r.get("resource_ids")),
                    "machine_ids": _list_str(r.get("machine_ids")),
                    # --- Nested dicts (truncated JSON strings) ---
                    "provider_data": _json_str(r.get("provider_data")),
                    "metadata": _json_str(r.get("metadata")),
                    "error_details": _json_str(r.get("error_details")),
                    # --- Versioning ---
                    "version": str(r.get("version") or ""),
                    # --- Raw for drawer ---
                    "raw": r,
                }
            )
        return rows

    @rx.var
    def sorted_rows(self) -> list[dict[str, Any]]:
        """Sorted version of request_rows for use with list_grid_view."""
        rows = list(self.request_rows)
        sk = self.sort_key
        sd = self.sort_dir
        if not sk:
            return rows

        def _key(r: dict[str, Any]) -> Any:
            v = r.get(sk, "")
            if v is None:
                return ""
            return v

        return sorted(rows, key=_key, reverse=(sd == "desc"))

    # ---------------------------------------------------------------------------
    # Events
    # ---------------------------------------------------------------------------

    def _normalize_visible_columns(self) -> None:
        """Ensure visible_columns uses the fenced format ,k1,k2,...,kN,."""
        vc = self.visible_columns
        if vc and not vc.startswith(","):
            keys = [k for k in vc.split(",") if k]
            self.visible_columns = "," + ",".join(keys) + "," if keys else ","

    @rx.event
    async def load(self) -> None:
        """Fetch first page of requests from the API for the current tab."""
        self._normalize_visible_columns()
        self.loading = True
        self.error = ""
        # Reset pagination for a fresh load
        self.next_cursor = ""
        self.api_total_count = 0
        try:
            if self.tab == "returns":
                res = await api.list_return_requests(limit=self.page_size)
            else:
                status_filter = _TAB_TO_STATUS.get(self.tab)
                res = await api.list_requests(status=status_filter, limit=self.page_size)
            raw = res.get("requests", [])
            # Sort descending by created_at so newest are at the top.
            self.requests = sorted(
                raw,
                key=lambda r: r.get("created_at") or "",
                reverse=True,
            )
            self.next_cursor = res.get("next_cursor") or ""
            self.api_total_count = int(res.get("total_count") or len(raw))
        except Exception as exc:
            self.error = f"Failed to load requests: {exc}"
        finally:
            self.loading = False

    @rx.event
    async def open_from_query(self):
        """If the URL carries ``?id=<request_id>``, open that request's drawer.

        Called from page on_mount AFTER ``load``, so ``self.requests`` is
        already populated and we can match the id to its raw dict.
        """
        try:
            rid = (self.router.page.params or {}).get("id", "")
        except Exception:
            rid = ""
        if not rid:
            return
        # Find the matching request in the just-loaded list.
        match = next((r for r in self.requests if str(r.get("request_id") or "") == rid), None)
        if match is None:
            # Not in current tab — fetch directly so the drawer can still open.
            try:
                res = await api.get_request(rid)
                requests_list = (res.get("requests") if isinstance(res, dict) else None) or []
                if requests_list:
                    match = requests_list[0]
            except Exception:
                return
        if match is None:
            return
        skel = _empty_request_skeleton()
        skel.update(match)
        self.selected_request = skel
        self.drawer_open = True
        yield RequestsState.poll_drawer_progress

    @rx.event
    async def set_tab(self, value: str):
        """Switch filter tab and reload."""
        self.tab = value
        yield RequestsState.load

    @rx.event
    async def refresh(self):
        """Manual refresh — same as load but always resets loading flag."""
        yield RequestsState.load

    _poll_started: bool = False

    @rx.event(background=True)
    async def auto_refresh(self) -> None:
        """Background polling task driven by auto_refresh_enabled and auto_refresh_interval.

        MUST be ``background=True``. A non-background ``@rx.event`` that
        ``await``s holds the state lock for the duration of the sleep,
        which blocks every other event handler on this state (drawer
        clicks, tab switches, etc.) for the full polling interval.
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
                    page_size = self.page_size
                    tab = self.tab
                if tab == "returns":
                    res = await api.list_return_requests(limit=page_size)
                else:
                    status_filter = _TAB_TO_STATUS.get(tab)
                    res = await api.list_requests(status=status_filter, limit=page_size)
                async with self:
                    raw = res.get("requests", [])
                    self.requests = sorted(
                        raw,
                        key=lambda r: r.get("created_at") or "",
                        reverse=True,
                    )
                    self.next_cursor = res.get("next_cursor") or ""
                    self.api_total_count = int(res.get("total_count") or len(raw))
                    self.last_refresh = datetime.datetime.now().strftime("%H:%M:%S")
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
        """Toggle the drawer live-poll on/off. Stored as 'true'/'false' for LocalStorage."""
        self.live_poll_enabled = "true" if checked else "false"

    @rx.event(background=True)
    async def load_more(self) -> None:
        """Append the next page of requests to the in-memory list.

        State-locking pattern: read ``loading_more`` and ``next_cursor``
        under the lock, release for the API call, reclaim in ``finally``
        to clear ``loading_more`` even on error.
        """
        async with self:
            if self.loading_more or not self.next_cursor:
                return
            self.loading_more = True
            cursor = self.next_cursor
            tab = self.tab
            page_size = self.page_size
        try:
            if tab == "returns":
                res = await api.list_return_requests(cursor=cursor, limit=page_size)
            else:
                status_filter = _TAB_TO_STATUS.get(tab)
                res = await api.list_requests(status=status_filter, cursor=cursor, limit=page_size)
            new_rows = res.get("requests", [])
            async with self:
                combined = list(self.requests) + new_rows
                self.requests = sorted(
                    combined,
                    key=lambda r: r.get("created_at") or "",
                    reverse=True,
                )
                self.next_cursor = res.get("next_cursor") or ""
                self.api_total_count = int(res.get("total_count") or self.api_total_count)
        except Exception as e:
            async with self:
                self.error = f"Failed to load more requests: {e}"
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
        yield RequestsState.load

    @rx.event
    def open_drawer(self, req: dict[str, Any]):
        """Open the detail drawer for the given request row."""
        self.drawer_open = True
        self.sync_error = ""
        if req:
            skel = _empty_request_skeleton()
            skel.update(req)
            self.selected_request = skel
        yield RequestsState.poll_drawer_progress

    @rx.event
    async def close_drawer(self) -> None:
        self.drawer_open = False
        # Reset to the empty skeleton (NOT {}). Reflex infers the Var
        # schema from this dict's keys; an empty dict would strip every
        # subscript binding from the next render.
        self.selected_request = _empty_request_skeleton()

    @rx.event(background=True)
    async def poll_drawer_progress(self):
        """Poll the open request every 2s until terminal or drawer closes.

        Snapshots the request_id at start; aborts on drawer close OR if
        selected_request switches to a different request (user opened
        another drawer). Each switch spawns a fresh poll task that races
        safely with any in-flight one — the older task aborts on its
        next iteration when it sees the rid changed.

        Respects ``live_poll_enabled``: when the user pauses live updates
        the loop sleeps briefly and rechecks the flag without hitting the
        API, so toggling back on resumes within ~2 s.
        """
        async with self:
            rid = str((self.selected_request or {}).get("request_id") or "")
            if not rid or not self.drawer_open:
                return
        while True:
            async with self:
                if not self.drawer_open:
                    return
                current_rid = str((self.selected_request or {}).get("request_id") or "")
                if current_rid != rid:
                    return
                paused = self.live_poll_enabled != "true"
            if paused:
                await asyncio.sleep(2)
                continue
            try:
                res = await api.get_request(rid)
            except Exception:
                await asyncio.sleep(2)
                continue
            async with self:
                current_rid = str((self.selected_request or {}).get("request_id") or "")
                if not self.drawer_open or current_rid != rid:
                    return
                # Unwrap envelope: GET /requests/{id}/status returns
                # {"requests": [...], "message": ..., "count": 1}, NOT a flat request DTO.
                requests_list = (res.get("requests") if isinstance(res, dict) else None) or []
                payload = (
                    requests_list[0] if requests_list else (res if isinstance(res, dict) else None)
                )
                if payload and payload.get("request_id"):
                    skel = _empty_request_skeleton()
                    skel.update(payload)
                    self.selected_request = skel
                status = (self.selected_request or {}).get("status") or ""
                if status.lower() in _TERMINAL_STATUSES:
                    return
            await asyncio.sleep(2)

    @rx.event
    def set_drawer_open(self, value: bool) -> None:
        """Two-way binding for the dialog's open state (ESC / backdrop)."""
        self.drawer_open = value
        if not value:
            self.selected_request = _empty_request_skeleton()

    @rx.event
    async def refresh_drawer(self) -> None:
        """Refresh the currently open request from the provider.

        Hits ``GET /requests/{id}/status`` (verbose=True), which performs
        a read-through sync against the provider, persists the updated
        machines, and returns the latest DTO. Also patches the row in
        ``self.requests`` so the list behind the drawer reflects the
        new state.
        """
        rid = (self.selected_request or {}).get("request_id")
        if not rid:
            return
        self.syncing_drawer = True
        self.sync_error = ""
        try:
            res = await api.get_request(rid)
            fresh_list = res.get("requests", [])
            if fresh_list:
                fresh = {**_empty_request_skeleton(), **fresh_list[0]}
                self.selected_request = fresh
                self.requests = [fresh if r.get("request_id") == rid else r for r in self.requests]
            self.last_sync_time = datetime.datetime.now().strftime("%H:%M:%S")
        except Exception as exc:
            self.sync_error = f"Sync failed: {exc}"
        finally:
            self.syncing_drawer = False

    # -----------------------------------------------------------------------
    # Events — row selection / bulk sync
    # -----------------------------------------------------------------------

    @rx.event
    def toggle_select(self, request_id: str) -> None:
        ids = list(self.selected_ids)
        if request_id in ids:
            ids.remove(request_id)
        else:
            ids.append(request_id)
        self.selected_ids = ids

    @rx.event
    def toggle_select_all(self) -> None:
        visible_ids = [r.get("request_id", "") for r in self.requests if r.get("request_id")]
        if self.all_visible_selected:
            self.selected_ids = [sid for sid in self.selected_ids if sid not in visible_ids]
        else:
            merged = set(self.selected_ids)
            merged.update(visible_ids)
            self.selected_ids = list(merged)

    @rx.event
    def clear_selection(self) -> None:
        self.selected_ids = []

    @rx.event
    async def sync_row(self, request_id: str) -> None:
        """Sync a single row from the provider without opening the drawer.

        Hits the same per-id sync path the drawer uses and patches the
        matching row in the page list so the table reflects the new
        state. Shares ``bulk_syncing`` for the spinner since a row sync
        also blocks the toolbar.
        """
        if not request_id:
            return
        self.bulk_syncing = True
        self.bulk_sync_error = ""
        try:
            res = await api.get_request(request_id)
            fresh_list = res.get("requests", [])
            if fresh_list:
                fresh = {**_empty_request_skeleton(), **fresh_list[0]}
                self.requests = [
                    fresh if r.get("request_id") == request_id else r for r in self.requests
                ]
                # Update the drawer if it's currently showing this row.
                if self.selected_request.get("request_id") == request_id:
                    self.selected_request = fresh
            self.last_sync_time = datetime.datetime.now().strftime("%H:%M:%S")
        except Exception as exc:
            self.bulk_sync_error = f"Sync failed for {request_id}: {exc}"
        finally:
            self.bulk_syncing = False

    @rx.event
    async def sync_selected(self) -> None:
        """Read-through-sync every selected request from the provider.

        Posts to ``/api/v1/requests/status`` with the selected IDs. The
        server-side orchestrator persists each refreshed request, so on
        completion we reload the list to surface the new statuses.
        """
        ids = list(self.selected_ids)
        if not ids:
            return
        self.bulk_syncing = True
        self.bulk_sync_error = ""
        try:
            await api.batch_get_request_status(ids, verbose=True)
            self.last_sync_time = datetime.datetime.now().strftime("%H:%M:%S")
            # Reload the list so the rows reflect the new persisted state.
            if self.tab == "returns":
                res = await api.list_return_requests(limit=self.page_size)
            else:
                status_filter = _TAB_TO_STATUS.get(self.tab)
                res = await api.list_requests(status=status_filter, limit=self.page_size)
            raw = res.get("requests", [])
            self.requests = sorted(raw, key=lambda r: r.get("created_at") or "", reverse=True)
            self.next_cursor = res.get("next_cursor") or ""
            self.api_total_count = int(res.get("total_count") or len(raw))
        except Exception as exc:
            self.bulk_sync_error = f"Bulk sync failed: {exc}"
        finally:
            self.bulk_syncing = False

    @rx.event(background=True)
    async def cancel_selected(self):
        """Cancel every selected request that is not already terminal.

        background=True so the Cancel Selected button grays out immediately.
        Called after the confirmation dialog is accepted.
        """
        async with self:
            if self.bulk_cancelling:
                return  # re-entrancy guard
            self.confirm_cancel_selected_open = False
            ids = [
                sid
                for sid in self.selected_ids
                if (
                    sid
                    and (
                        next(
                            (
                                r.get("status", "")
                                for r in self.requests
                                if r.get("request_id") == sid
                            ),
                            "",
                        )
                        or ""
                    ).lower()
                    not in _TERMINAL_STATUSES
                )
            ]
            if not ids:
                return
            self.bulk_cancelling = True
            self.bulk_cancel_error = ""
        errors: list[str] = []
        for rid in ids:
            try:
                await api.cancel_request(rid)
            except Exception as exc:
                errors.append(f"{rid}: {exc}")
        async with self:
            if errors:
                self.bulk_cancel_error = (
                    "Some cancels failed: "
                    + "; ".join(errors[:3])
                    + (f" (+{len(errors) - 3} more)" if len(errors) > 3 else "")
                )
            self.bulk_cancelling = False
            self.selected_ids = []
        yield RequestsState.load

    @rx.event
    async def sync_all(self) -> None:
        """Read-through-sync every request in the current list view.

        Mirrors ``sync_selected`` but uses every visible request_id, so
        the operator does not need to manually select. Useful on the
        Dashboard / Requests page after a provider hiccup.
        """
        ids = [r.get("request_id", "") for r in self.requests if r.get("request_id")]
        if not ids:
            return
        self.bulk_syncing = True
        self.bulk_sync_error = ""
        try:
            await api.batch_get_request_status(ids, verbose=True)
            self.last_sync_time = datetime.datetime.now().strftime("%H:%M:%S")
            # Reload the list so the rows reflect the new persisted state.
            if self.tab == "returns":
                res = await api.list_return_requests(limit=self.page_size)
            else:
                status_filter = _TAB_TO_STATUS.get(self.tab)
                res = await api.list_requests(status=status_filter, limit=self.page_size)
            raw = res.get("requests", [])
            self.requests = sorted(raw, key=lambda r: r.get("created_at") or "", reverse=True)
            self.next_cursor = res.get("next_cursor") or ""
            self.api_total_count = int(res.get("total_count") or len(raw))
        except Exception as exc:
            self.bulk_sync_error = f"Sync All failed: {exc}"
        finally:
            self.bulk_syncing = False

    @rx.event
    def open_confirm_cancel_all(self) -> None:
        self.confirm_cancel_all_open = True

    @rx.event
    def dismiss_cancel_all(self) -> None:
        self.confirm_cancel_all_open = False

    @rx.event
    def open_confirm_cancel_selected(self) -> None:
        self.confirm_cancel_selected_open = True

    @rx.event
    def dismiss_cancel_selected(self) -> None:
        self.confirm_cancel_selected_open = False

    @rx.event(background=True)
    async def cancel_all_active(self):
        """Cancel every non-terminal request in the current list view.

        Each cancel goes through ``CancelRequestOrchestrator`` which
        returns any allocated machines (terminating them at the provider)
        before flipping the request to CANCELLED. Failures on individual
        requests are collected and surfaced at the end so a single
        failure does not abort the batch.

        background=True so the Cancel All button grays out (loading=bulk_cancelling)
        immediately when clicked instead of waiting for the state lock.
        """
        async with self:
            if self.bulk_cancelling:
                return  # re-entrancy guard
            active_ids = [
                r.get("request_id", "")
                for r in self.requests
                if r.get("request_id") and (r.get("status") or "").lower() not in _TERMINAL_STATUSES
            ]
            self.confirm_cancel_all_open = False
            if not active_ids:
                return
            self.bulk_cancelling = True
            self.bulk_cancel_error = ""
        errors: list[str] = []
        for rid in active_ids:
            try:
                await api.cancel_request(rid)
            except Exception as exc:
                errors.append(f"{rid}: {exc}")
        async with self:
            if errors:
                self.bulk_cancel_error = (
                    "Some cancels failed: "
                    + "; ".join(errors[:3])
                    + (f" (+{len(errors) - 3} more)" if len(errors) > 3 else "")
                )
            self.bulk_cancelling = False
        yield RequestsState.load

    @rx.event
    async def confirm_cancel(self) -> None:
        """Open the cancel confirmation dialog for the currently open drawer."""
        self.cancel_target_request_id = self.selected_request.get("request_id", "")
        self.confirm_cancel_open = True

    @rx.event
    async def confirm_cancel_row(self, request_id: str) -> None:
        """Open the cancel confirmation dialog from a table row action."""
        self.cancel_target_request_id = request_id
        self.confirm_cancel_open = True

    @rx.event
    async def dismiss_cancel(self) -> None:
        self.confirm_cancel_open = False
        self.cancel_target_request_id = ""

    @rx.event(background=True)
    async def do_cancel(self):
        """Execute the cancellation after confirmation.

        background=True so cancel buttons gray out (loading=cancelling) immediately
        when clicked instead of waiting for the state lock to release.
        """
        async with self:
            if self.cancelling:
                return  # re-entrancy guard
            rid = self.cancel_target_request_id
            if not rid:
                return
            self.confirm_cancel_open = False
            self.cancel_target_request_id = ""
            self.cancelling = True
            self.error = ""
        try:
            await api.cancel_request(rid)
        except Exception as exc:
            async with self:
                self.error = f"Cancel failed: {exc}"
                self.cancelling = False
            return
        async with self:
            # Close drawer if we were cancelling the open request
            if self.selected_request.get("request_id") == rid:
                self.drawer_open = False
                self.selected_request = _empty_request_skeleton()
            self.cancelling = False
        yield RequestsState.load

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
        locked_keys = {c.key for c in ALL_REQUEST_COLUMNS if c.lockable}
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


# ---------------------------------------------------------------------------
# Component helpers
# ---------------------------------------------------------------------------


def _request_card(row: Any) -> rx.Component:
    """Card renderer for grid view mode."""
    return rx.card(
        rx.vstack(
            # Header: request ID + status badge
            rx.hstack(
                rx.code(row["request_id"], size="1"),
                rx.spacer(),
                request_status_badge(row["status"]),
                align="center",
                width="100%",
            ),
            rx.divider(),
            # Type + template
            rx.hstack(
                rx.badge(row["request_type"], variant="outline", size="1"),
                rx.text(row["template_id"], size="2", color=rx.color("gray", 11)),
                spacing="2",
                align="center",
                flex_wrap="wrap",
            ),
            # Progress bar (inline)
            rx.vstack(
                rx.box(
                    rx.box(
                        height="0.375rem",
                        border_radius="full",
                        background=rx.match(
                            row["status"],
                            ("failed", rx.color("red", 9)),
                            ("complete", rx.color("green", 9)),
                            ("completed", rx.color("green", 9)),
                            rx.color("amber", 9),
                        ),
                        width=row["progress_pct"].to_string() + "%",
                        transition="width 0.3s ease",
                    ),
                    height="0.375rem",
                    background=rx.color("gray", 4),
                    border_radius="full",
                    overflow="hidden",
                    width="100%",
                ),
                rx.cond(
                    row["is_weighted"],
                    rx.hstack(
                        rx.text(row["fulfilled_units"], size="1", color=rx.color("gray", 10)),
                        rx.text("/", size="1", color=rx.color("gray", 10)),
                        rx.text(row["target_units"], size="1", color=rx.color("gray", 10)),
                        rx.text("units", size="1", color=rx.color("gray", 9)),
                        spacing="1",
                    ),
                    rx.hstack(
                        rx.text(row["successful_count"], size="1", color=rx.color("gray", 10)),
                        rx.text("/", size="1", color=rx.color("gray", 10)),
                        rx.text(row["requested_count"], size="1", color=rx.color("gray", 10)),
                        spacing="1",
                    ),
                ),
                spacing="1",
                align="start",
                width="100%",
            ),
            # Counts row
            rx.hstack(
                rx.text("Failed:", size="1", color=rx.color("gray", 10)),
                rx.text(row["failed_count"], size="1", color=rx.color("gray", 11)),
                rx.text("Returned:", size="1", color=rx.color("gray", 10)),
                rx.text(row["returned_count"], size="1", color=rx.color("gray", 11)),
                spacing="2",
                align="center",
                flex_wrap="wrap",
            ),
            # Created + duration
            rx.hstack(
                rx.icon("clock", size=14, color=rx.color("gray", 10)),
                rx.text(row["created_at"], size="1", color=rx.color("gray", 11)),
                rx.cond(
                    row["duration_fmt"] != "—",
                    rx.text(row["duration_fmt"], size="1", color=rx.color("gray", 10)),
                    rx.fragment(),
                ),
                spacing="2",
                align="center",
            ),
            rx.divider(),
            # Footer actions
            rx.hstack(
                rx.icon_button(
                    rx.icon("eye", size=14),
                    size="1",
                    variant="ghost",
                    on_click=RequestsState.open_drawer(row["raw"]),
                    title="View detail",
                ),
                rx.icon_button(
                    rx.icon("cloud-download", size=14),
                    size="1",
                    variant="ghost",
                    color_scheme="blue",
                    loading=RequestsState.bulk_syncing,
                    on_click=RequestsState.sync_row(row["request_id"]),
                    title="Sync from provider",
                ),
                rx.cond(
                    ~row["is_terminal"],
                    rx.icon_button(
                        rx.icon("x", size=14),
                        size="1",
                        variant="ghost",
                        color_scheme="red",
                        loading=RequestsState.cancelling,
                        on_click=RequestsState.confirm_cancel_row(row["request_id"]),
                        title="Cancel request",
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
        on_click=RequestsState.open_drawer(row["raw"]),
        width="100%",
    )


def _progress_bar(row) -> rx.Component:
    """Thin progress bar for the legacy table row. Reads the pre-formatted
    row dict (which already contains weighted-capacity fields) so it renders
    the same numbers as ``_progress_bar_formatter`` and the card view."""
    bar_color = rx.match(
        row["status"],
        ("failed", rx.color("red", 9)),
        ("complete", rx.color("green", 9)),
        ("completed", rx.color("green", 9)),
        rx.color("amber", 9),
    )
    return rx.vstack(
        rx.box(
            rx.box(
                height="0.375rem",
                border_radius="full",
                background=bar_color,
                width=row["progress_pct"].to_string() + "%",
                transition="width 0.3s ease",
            ),
            height="0.375rem",
            background=rx.color("gray", 4),
            border_radius="full",
            overflow="hidden",
            width="100%",
        ),
        rx.cond(
            row["is_weighted"],
            rx.hstack(
                rx.text(row["fulfilled_units"], size="1", color=rx.color("gray", 10)),
                rx.text("/", size="1", color=rx.color("gray", 10)),
                rx.text(row["target_units"], size="1", color=rx.color("gray", 10)),
                rx.text("units", size="1", color=rx.color("gray", 9)),
                spacing="1",
            ),
            rx.hstack(
                rx.text(row["successful_count"], size="1", color=rx.color("gray", 10)),
                rx.text("/", size="1", color=rx.color("gray", 10)),
                rx.text(row["requested_count"], size="1", color=rx.color("gray", 10)),
                spacing="1",
            ),
        ),
        spacing="1",
        align="center",
        min_width="80px",
    )


def _request_row(row) -> rx.Component:
    """Single table row for a request — consumes pre-formatted Var dict from
    RequestsState.request_rows."""
    return rx.table.row(
        # Per-row selection checkbox
        rx.table.cell(
            rx.checkbox(
                checked=RequestsState.selected_ids.contains(row["request_id"]),
                on_change=RequestsState.toggle_select(row["request_id"]),
            ),
            vertical_align="middle",
            width="36px",
        ),
        # Request ID — button (not rx.link without href, which silently swallows on_click)
        rx.table.cell(
            rx.button(
                rx.code(row["request_id"], size="1"),
                on_click=RequestsState.open_drawer(row["raw"]),
                variant="ghost",
                size="1",
                cursor="pointer",
            ),
            vertical_align="middle",
        ),
        # Status
        rx.table.cell(
            request_status_badge(row["status"]),
            vertical_align="middle",
        ),
        # Type
        rx.table.cell(
            rx.badge(row["request_type"], variant="outline", size="1"),
            vertical_align="middle",
        ),
        # Template
        rx.table.cell(
            rx.text(row["template_id"], size="2"),
            vertical_align="middle",
        ),
        # Progress bar (fulfilled / requested, weighted-aware)
        rx.table.cell(
            _progress_bar(row),
            vertical_align="middle",
        ),
        # Created
        rx.table.cell(
            rx.text(row["created"], size="1", color=rx.color("gray", 11)),
            vertical_align="middle",
        ),
        # Actions: icon-only buttons with hover tooltips. View opens the
        # drawer in place; Sync hits /requests/{id}/status; Cancel
        # (non-terminal rows only) opens the confirmation dialog.
        rx.table.cell(
            rx.hstack(
                rx.icon_button(
                    rx.icon("eye", size=14),
                    size="1",
                    variant="ghost",
                    on_click=RequestsState.open_drawer(row["raw"]),
                    title="View detail",
                ),
                rx.icon_button(
                    rx.icon("cloud-download", size=14),
                    size="1",
                    variant="ghost",
                    color_scheme="blue",
                    loading=RequestsState.bulk_syncing,
                    on_click=RequestsState.sync_row(row["request_id"]),
                    title="Sync from provider",
                ),
                rx.cond(
                    ~row["is_terminal"],
                    rx.icon_button(
                        rx.icon("x", size=14),
                        size="1",
                        variant="ghost",
                        color_scheme="red",
                        loading=RequestsState.cancelling,
                        on_click=RequestsState.confirm_cancel_row(row["request_id"]),
                        title="Cancel request",
                    ),
                    rx.fragment(),
                ),
                spacing="2",
                align="center",
            ),
            vertical_align="middle",
        ),
        _hover={"background": rx.color("gray", 2)},
    )


def _filter_row() -> rx.Component:
    """Canonical filter row: filter pills + search input + spacer + refresh_control.

    Layout matches the canonical pattern:
        [Filter pills] [Search input] <spacer> [refresh_control]
    """
    return rx.hstack(
        rx.hstack(
            *[
                rx.button(
                    f"{label}",
                    size="2",
                    variant=rx.cond(RequestsState.tab == value, "solid", "soft"),
                    color_scheme=rx.cond(RequestsState.tab == value, "blue", "gray"),
                    radius="full",
                    on_click=RequestsState.set_tab(value),
                    role="tab",
                    aria_selected=rx.cond(RequestsState.tab == value, "true", "false"),
                )
                for value, label in _TABS
            ],
            role="tablist",
            spacing="2",
            flex_wrap="wrap",
            align="center",
        ),
        rx.input(
            placeholder="Search…",
            value=RequestsState.search_text,
            on_change=RequestsState.set_search_text,
            width="240px",
        ),
        rx.spacer(),
        refresh_control(
            enabled=RequestsState.auto_refresh_enabled,
            interval=RequestsState.auto_refresh_interval,
            on_toggle=RequestsState.toggle_auto_refresh,
            on_set_interval=RequestsState.set_auto_refresh_interval,
            on_manual_refresh=RequestsState.refresh,
            last_refresh_text=RequestsState.last_refresh,
            loading=RequestsState.loading,
        ),
        spacing="2",
        flex_wrap="wrap",
        align="center",
        margin_bottom="1rem",
        width="100%",
    )


def _empty_state() -> rx.Component:
    return empty_state(
        icon="inbox",
        title="No requests found",
        description="No machine requests match the current filter.",
    )


def _loading_skeleton() -> rx.Component:
    """Animated loading placeholders while data fetches.

    TODO: replace with loading_skeleton(rows=5, height="2.5rem") from
    ..components.loading_skeleton once the components agent ships it.
    """
    return rx.vstack(
        *[rx.skeleton(height="40px", width="100%", border_radius="0.5rem") for _ in range(5)],
        spacing="2",
        width="100%",
    )


def _confirm_dialog() -> rx.Component:
    """Cancel confirmation alert dialog."""
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Cancel Request"),
            rx.alert_dialog.description(
                rx.vstack(
                    rx.text(
                        "Cancel request ",
                        rx.code(RequestsState.cancel_target_request_id, size="1"),
                        "?",
                        as_="span",
                    ),
                    rx.text(
                        "Any machines already allocated to this request will be "
                        "returned to the provider (terminated) before the request "
                        "is marked cancelled. This cannot be undone.",
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
                        on_click=RequestsState.dismiss_cancel,
                    ),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Cancel Request",
                        color_scheme="red",
                        on_click=RequestsState.do_cancel,
                    ),
                ),
                spacing="3",
                justify="end",
                margin_top="4",
            ),
        ),
        open=RequestsState.confirm_cancel_open,
    )


def _count_badge() -> rx.Component:
    """Shows loaded / total request counts and page-size selector."""
    return rx.hstack(
        rx.text("Showing", size="2", color=rx.color("gray", 11)),
        rx.badge(
            RequestsState.loaded_count.to_string()
            + " of "
            + RequestsState.total_requests.to_string(),
            variant="soft",
            color_scheme="gray",
            size="1",
        ),
        rx.text("requests", size="2", color=rx.color("gray", 11)),
        rx.select(
            ["50", "100", "200", "500"],
            value=RequestsState.page_size.to_string(),
            on_change=RequestsState.set_page_size,
            size="1",
            width="80px",
        ),
        rx.text("per page", size="2", color=rx.color("gray", 11)),
        spacing="2",
        align="center",
        margin_bottom="3",
    )


def _selection_actions() -> rx.Component:
    """Inline selection-action chip rendered inside the toolbar when ≥1
    request is selected. Order matches the bulk-all actions below it:
    [Sync Selected (N)] [Cancel Selected (N)].
    """
    return rx.cond(
        RequestsState.has_selection,
        rx.hstack(
            rx.button(
                rx.icon("cloud-download", size=14),
                "Sync Selected (" + RequestsState.selected_count.to_string() + ")",
                size="2",
                variant="soft",
                color_scheme="blue",
                loading=RequestsState.bulk_syncing,
                on_click=RequestsState.sync_selected,
                title="Pull live state from the provider for each selected request",
            ),
            rx.button(
                rx.icon("x", size=14),
                "Cancel Selected (" + RequestsState.selected_count.to_string() + ")",
                size="2",
                variant="soft",
                color_scheme="red",
                loading=RequestsState.bulk_cancelling,
                on_click=RequestsState.open_confirm_cancel_selected,
                title="Cancel every selected non-terminal request",
            ),
            spacing="2",
            align="center",
        ),
        rx.fragment(),
    )


def _toolbar() -> rx.Component:
    """Toolbar: count badge + spacer + primary action buttons + view toggle + column picker.

    Always-visible actions (New Request, Sync All, Cancel All Active) live here.
    Selection-specific actions are in _selection_bar() above.
    """
    return rx.hstack(
        _count_badge(),
        rx.spacer(),
        # Right-side action buttons
        rx.flex(
            rx.button(
                rx.icon("send", size=14),
                "New Request",
                size="2",
                color_scheme="blue",
                on_click=RequestModalState.open_picker,
                title="Pick a template and submit a new machine request",
            ),
            # Inline selection-action chip — Sync Sel / Cancel Sel (Selected before All)
            _selection_actions(),
            rx.button(
                rx.icon("cloud-download", size=14),
                "Sync All",
                size="2",
                variant="soft",
                color_scheme="blue",
                disabled=~RequestsState.has_requests,
                loading=RequestsState.bulk_syncing,
                on_click=RequestsState.sync_all,
                title="Pull live state from the provider for every visible request",
            ),
            rx.cond(
                RequestsState.visible_active_count > 0,
                rx.button(
                    rx.icon("x", size=14),
                    "Cancel All Active (" + RequestsState.visible_active_count.to_string() + ")",
                    size="2",
                    variant="soft",
                    color_scheme="red",
                    loading=RequestsState.bulk_cancelling,
                    on_click=RequestsState.open_confirm_cancel_all,
                    title="Cancel every non-terminal request; their machines are returned",
                ),
                rx.fragment(),
            ),
            # View toggle and column picker
            view_toggle(
                mode=RequestsState.view_mode,
                on_change=RequestsState.set_view_mode,
            ),
            column_picker(
                columns=REQUEST_COLUMNS,
                visible_columns=RequestsState.visible_columns,
                on_toggle=RequestsState.toggle_column,
            ),
            flex_wrap="wrap",
            row_gap="0.5rem",
            gap="0.5rem",
            align="center",
        ),
        align="center",
        margin_top="0.5rem",
        margin_bottom="1rem",
        width="100%",
        flex_wrap="wrap",
        row_gap="0.5rem",
    )


def _confirm_cancel_all_dialog() -> rx.Component:
    """Confirmation dialog for the toolbar "Cancel All Active" action.

    Distinct from the per-row confirmation; this one calls out the
    plurality and reminds the operator that machines will be returned.
    """
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Cancel All Active Requests"),
            rx.alert_dialog.description(
                rx.vstack(
                    rx.text(
                        "Cancel ",
                        rx.code(RequestsState.visible_active_count.to_string(), size="1"),
                        " active request(s)?",
                        as_="span",
                    ),
                    rx.text(
                        "Machines already allocated to these requests will be "
                        "returned to the provider (terminated) before each "
                        "request is marked cancelled. This cannot be undone.",
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
                        on_click=RequestsState.dismiss_cancel_all,
                    ),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Cancel All",
                        color_scheme="red",
                        loading=RequestsState.bulk_cancelling,
                        on_click=RequestsState.cancel_all_active,
                    ),
                ),
                spacing="3",
                justify="end",
                margin_top="1.5rem",
            ),
        ),
        open=RequestsState.confirm_cancel_all_open,
    )


def _confirm_cancel_selected_dialog() -> rx.Component:
    """Confirmation dialog for the toolbar "Cancel Selected" action.

    Mirrors ``_confirm_cancel_all_dialog`` but is scoped to the checkbox
    selection.  Confirms the count and warns that machines will be returned.
    """
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Cancel Selected Requests"),
            rx.alert_dialog.description(
                rx.vstack(
                    rx.text(
                        "Cancel ",
                        rx.code(RequestsState.selected_count.to_string(), size="1"),
                        " selected request(s)?",
                        as_="span",
                    ),
                    rx.text(
                        "Machines already allocated to these requests will be "
                        "returned to the provider (terminated) before each "
                        "request is marked cancelled. This cannot be undone.",
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
                        on_click=RequestsState.dismiss_cancel_selected,
                    ),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Cancel Selected",
                        color_scheme="red",
                        loading=RequestsState.bulk_cancelling,
                        on_click=RequestsState.cancel_selected,
                    ),
                ),
                spacing="3",
                justify="end",
                margin_top="1.5rem",
            ),
        ),
        open=RequestsState.confirm_cancel_selected_open,
    )


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------


def _requests_load_more() -> rx.Component:
    """Load-more button shown when a next-page cursor is available."""
    return rx.cond(
        RequestsState.next_cursor != "",
        rx.center(
            rx.button(
                rx.cond(
                    RequestsState.loading_more,
                    rx.spinner(size="2"),
                    rx.icon("chevrons-down", size=16),
                ),
                rx.cond(
                    RequestsState.loading_more,
                    "Loading…",
                    "Load more",
                ),
                on_click=RequestsState.load_more,
                disabled=RequestsState.loading_more,
                variant="soft",
                color_scheme="gray",
                size="2",
            ),
            width="100%",
            padding_top="0.75rem",
        ),
        rx.fragment(),
    )


def requests_page() -> rx.Component:
    """The Requests page, registered in orb_ui.py routing."""
    return page(
        "Requests",
        list_page_shell(
            error_banner=rx.cond(
                RequestsState.error != "",
                error_callout(RequestsState.error),
                rx.fragment(),
            ),
            banners=[
                rx.cond(
                    RequestsState.bulk_sync_error != "",
                    error_callout(RequestsState.bulk_sync_error),
                    rx.fragment(),
                ),
                request_success_banner(),
            ],
            filter_row=_filter_row(),
            toolbar=_toolbar(),
            grid=list_grid_view(
                rows=RequestsState.sorted_rows,
                columns=ALL_REQUEST_COLUMNS,
                view_mode=RequestsState.view_mode,
                visible_columns=RequestsState.visible_columns,
                sort_key=RequestsState.sort_key,
                sort_dir=RequestsState.sort_dir,
                card_renderer=_request_card,
                on_row_click=None,  # row click handled per-cell
                on_sort=RequestsState.set_sort,
            ),
            load_more=_requests_load_more(),
            empty=_empty_state(),
            is_loading=RequestsState.loading & (RequestsState.loaded_count == 0),
            is_empty=~RequestsState.has_requests,
            loading_skeleton=_loading_skeleton(),
            dialogs=[
                _confirm_dialog(),
                _confirm_cancel_all_dialog(),
                _confirm_cancel_selected_dialog(),
                request_drawer(RequestsState),
                machine_drawer(MachineQuickViewState),
                request_modal(),
            ],
        ),
        on_mount=[
            RequestsState.load,
            RequestsState.open_from_query,
            RequestsState.auto_refresh,
        ],
    )
