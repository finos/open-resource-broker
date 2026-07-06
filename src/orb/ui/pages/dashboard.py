"""Dashboard — stat cards + recent activity feed + activity-insight charts."""

from __future__ import annotations

import asyncio
import datetime
from collections import Counter, defaultdict
from typing import Any

import httpx
import reflex as rx

from .. import api
from ..components.column_picker import column_picker
from ..components.empty_state import empty_state
from ..components.error_callout import error_callout
from ..components.layout import page
from ..components.list_grid_view import ColumnDef
from ..components.machine_drawer import machine_drawer
from ..components.machine_quick_view import MachineQuickViewState
from ..components.refresh_control import refresh_control
from ..components.request_drawer import request_drawer
from ..components.status_badge import request_status_badge
from ..state import AppState
from .requests import RequestsState

# Activity-feed column definitions. Request ID + Lifecycle are locked
# (always visible) — the stepper is the unique value-add of this combined
# view and the request_id is needed to identify rows. Everything else
# can be toggled off via the column picker.
ACTIVITY_COLUMNS: list[ColumnDef] = [
    ColumnDef("time", "Time", default_visible=True, lockable=False),
    ColumnDef("request_id", "Request ID", default_visible=True, lockable=True),
    ColumnDef("type", "Type", default_visible=True, lockable=False),
    ColumnDef("status", "Status", default_visible=True, lockable=False),
    ColumnDef("template_id", "Template", default_visible=True, lockable=False),
    ColumnDef("fulfilled", "Fulfilled", default_visible=True, lockable=False),
    ColumnDef("lifecycle", "Lifecycle", default_visible=True, lockable=True),
]

_ACTIVITY_VISIBLE_DEFAULT = (
    "," + ",".join(c.key for c in ACTIVITY_COLUMNS if c.default_visible) + ","
)


# Colour palette used by the donut chart slices
_STATUS_COLOURS: dict[str, str] = {
    "complete": "#22c55e",
    "completed": "#22c55e",
    "in_progress": "#3b82f6",
    "pending": "#f59e0b",
    "provisioning": "#f59e0b",
    "failed": "#ef4444",
    "error": "#ef4444",
    "cancelled": "#6b7280",
    "canceled": "#6b7280",
    "timeout": "#f97316",
    "partial": "#a78bfa",
}


class DashboardState(rx.State):
    """State for the dashboard page.

    Data is populated via the ``/system/dashboard`` aggregate endpoint which
    returns pre-rolled-up counts server-side (one round-trip instead of three).
    The raw ``recent_activity`` list from the response drives both the activity
    table and the chart computed vars.
    """

    # Summary counts — populated from the /system/dashboard response.
    # Shapes mirror DashboardSummaryOutput (dashboard_summary.py).
    _machines: dict[str, Any] = {}  # {total, by_status}
    _requests: dict[str, Any] = {}  # {total, in_flight, by_status}
    _templates: dict[str, Any] = {}  # {total, by_provider_api}
    # Recent activity rows returned by the aggregate endpoint (latest 10).
    # Each row: {request_id, status, request_type, template_id, created_at,
    #            successful_count, requested_count}
    recent_requests: list[dict[str, Any]] = []

    loading: bool = False
    error: str = ""
    last_refresh: str = ""
    _poll_started: bool = False

    # Auto-refresh preferences (persisted in localStorage)
    auto_refresh_enabled: str = rx.LocalStorage("false", name="orb-dashboard-auto-refresh-enabled")
    auto_refresh_interval: str = rx.LocalStorage("10", name="orb-dashboard-auto-refresh-interval")

    # Activity-table column visibility (persisted in localStorage).
    # Fenced comma-separated keys, e.g. ",time,request_id,status,lifecycle,".
    visible_columns: str = rx.LocalStorage(
        _ACTIVITY_VISIBLE_DEFAULT, name="orb-dashboard-activity-visible-columns"
    )

    @rx.event
    def toggle_column(self, key: str, checked: bool) -> None:
        """Add or remove a column key from the fenced visible_columns string.

        Lockable columns are filtered out at the picker layer so this only
        ever runs for togglable columns. Fenced format (leading + trailing
        comma) lets ``.contains("," + key + ",")`` avoid substring matches.
        """
        cur = self.visible_columns or ""
        token = "," + key + ","
        if checked:
            if token not in cur:
                cur = cur if cur.startswith(",") else "," + cur
                cur = cur if cur.endswith(",") else cur + ","
                cur = cur + key + ","
        else:
            cur = cur.replace(token, ",")
        # Normalise: collapse double commas, ensure leading/trailing comma.
        while ",," in cur:
            cur = cur.replace(",,", ",")
        if not cur.startswith(","):
            cur = "," + cur
        if not cur.endswith(","):
            cur = cur + ","
        self.visible_columns = cur

    # ------------------------------------------------------------------ events

    @rx.event(background=True)
    async def load(self):
        """Initial dashboard load via the /system/dashboard aggregate endpoint.

        One round-trip replaces three separate list calls; the server rolls up
        counts so the UI never needs to reduce thousands of records client-side.
        Background so the lock is released during the I/O call and other
        handlers (drawer clicks, nav) remain responsive.
        """
        async with self:
            self.loading = True
            self.error = ""
        try:
            data = await api.get_dashboard_summary()
            templates_section = data.get("templates") or {}
            # /system/dashboard's templates count uses SQL count_by_column
            # against the storage strategy — file-based templates (loaded
            # from ``config/aws_templates.json`` for provider-owned samples)
            # aren't persisted to the SQL table and so report 0 there while
            # /api/v1/templates/ still lists them (its list endpoint reads
            # from the same file-backed strategy).  Reconcile by asking
            # the list endpoint for its ``total_count`` whenever the
            # aggregate says zero.
            if int(templates_section.get("total") or 0) == 0:
                try:
                    tlist = await api.list_templates(limit=1)
                    fallback_total = int(tlist.get("total_count") or 0)
                    if fallback_total > 0:
                        templates_section = {
                            **templates_section,
                            "total": fallback_total,
                        }
                except httpx.HTTPError:
                    # If the fallback fails just keep the aggregate's zero;
                    # the dashboard tile will still render correctly and
                    # onboarding banner logic still applies.
                    pass
            async with self:
                self._machines = data.get("machines") or {}
                self._requests = data.get("requests") or {}
                self._templates = templates_section
                self.recent_requests = data.get("recent_activity") or []
                self.last_refresh = datetime.datetime.now().strftime("%H:%M:%S")
        except httpx.HTTPError as e:
            async with self:
                self.error = f"API error: {e}"
        except Exception as e:
            async with self:
                self.error = f"Unexpected error: {e}"
        finally:
            async with self:
                self.loading = False

    @rx.event(background=True)
    async def auto_refresh(self):
        """Refresh data on a configurable interval. Single-flight via _poll_started so
        re-mounting the page does not spawn additional background loops.

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
                # Release lock before the single aggregate API call so other
                # event handlers remain responsive during the network round-trip.
                async with self:
                    self.loading = True
                    self.error = ""
                try:
                    data = await api.get_dashboard_summary()
                    templates_section = data.get("templates") or {}
                    if int(templates_section.get("total") or 0) == 0:
                        try:
                            tlist = await api.list_templates(limit=1)
                            fallback_total = int(tlist.get("total_count") or 0)
                            if fallback_total > 0:
                                templates_section = {
                                    **templates_section,
                                    "total": fallback_total,
                                }
                        except httpx.HTTPError:
                            # Best-effort fallback; ignore HTTP errors so dashboard refresh continues
                            pass
                    async with self:
                        self._machines = data.get("machines") or {}
                        self._requests = data.get("requests") or {}
                        self._templates = templates_section
                        self.recent_requests = data.get("recent_activity") or []
                        self.last_refresh = datetime.datetime.now().strftime("%H:%M:%S")
                except Exception as e:
                    async with self:
                        self.error = f"Refresh error: {e}"
                finally:
                    async with self:
                        self.loading = False
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

    # ---------------------------------------------------------------- computed

    @rx.var
    def total_machines(self) -> int:
        return int(self._machines.get("total") or 0)

    @rx.var
    def running_machines(self) -> int:
        by_status = self._machines.get("by_status") or {}
        return int(by_status.get("running") or 0)

    @rx.var
    def pending_machines(self) -> int:
        by_status = self._machines.get("by_status") or {}
        return int(by_status.get("pending") or 0) + int(by_status.get("provisioning") or 0)

    @rx.var
    def terminated_machines(self) -> int:
        by_status = self._machines.get("by_status") or {}
        return int(by_status.get("terminated") or 0) + int(by_status.get("returned") or 0)

    @rx.var
    def in_flight_requests(self) -> int:
        return int(self._requests.get("in_flight") or 0)

    @rx.var
    def failed_requests(self) -> int:
        """Requests that ended in a failure-shaped terminal state."""
        by_status = self._requests.get("by_status") or {}
        return (
            int(by_status.get("failed") or 0)
            + int(by_status.get("error") or 0)
            + int(by_status.get("timeout") or 0)
            + int(by_status.get("partial") or 0)
        )

    @rx.var
    def completed_requests(self) -> int:
        """Requests that finished cleanly.

        RequestStatus.COMPLETED has value "complete" (singular) — not
        "completed". Earlier the dashboard read the wrong key and the
        stat card never moved off zero.
        """
        by_status = self._requests.get("by_status") or {}
        return int(by_status.get("complete") or by_status.get("completed") or 0)

    @rx.var
    def total_templates(self) -> int:
        return int(self._templates.get("total") or 0)

    @rx.var
    def recent_activity(self) -> list[dict[str, Any]]:
        """Pre-formatted activity rows for the activity table.

        Source: ``recent_requests`` populated from the /system/dashboard
        aggregate response (latest 10 rows server-side).  Each row is
        normalised so the table template only needs simple field access.
        """
        rows: list[dict[str, Any]] = []
        for r in self.recent_requests:
            req_id = r.get("request_id") or ""
            created = r.get("created_at") or ""
            time_str = created[:19].replace("T", " ") if created else "—"
            fulfilled = int(r.get("successful_count") or r.get("fulfilled_units") or 0)
            rows.append(
                {
                    "request_id": req_id,
                    "short_id": req_id or "—",
                    "status": (r.get("status") or "").lower(),
                    "template_id": r.get("template_id") or "—",
                    "time": time_str,
                    "type": r.get("request_type") or r.get("type") or "acquire",
                    "fulfilled": str(fulfilled) if fulfilled else "—",
                    # Lifecycle timestamps — the /system/dashboard aggregate now
                    # forwards each of these; the inline stepper renders a coloured
                    # dot when present and dashed-gray when empty/None.
                    "created_at": created,
                    "started_at": r.get("started_at") or "",
                    "first_status_check": r.get("first_status_check") or "",
                    "last_status_check": r.get("last_status_check") or "",
                    "completed_at": r.get("completed_at") or "",
                    # Full request payload for the detail drawer.
                    "raw": r,
                }
            )
        return rows

    @rx.var
    def has_activity(self) -> bool:
        return len(self.recent_activity) > 0

    # -------------------------------------------------------- chart computed vars

    @rx.var
    def status_donut_data(self) -> list[dict[str, Any]]:
        """Aggregate requests by status for the donut chart.

        Derived from the ``by_status`` counts in the aggregate response so
        the chart reflects the full dataset (not just the 10 recent rows).
        Shape: [{"name": "complete", "value": 12, "fill": "#22c55e"}, ...]
        """
        by_status: dict[str, Any] = self._requests.get("by_status") or {}
        counts: Counter[str] = Counter(
            {k: int(v or 0) for k, v in by_status.items() if int(v or 0) > 0}
        )
        result: list[dict[str, Any]] = []
        for status, count in counts.most_common():
            colour = _STATUS_COLOURS.get(status, "#94a3b8")
            result.append({"name": status, "value": count, "fill": colour})
        return result

    @rx.var
    def fulfillment_trend_data(self) -> list[dict[str, Any]]:
        """Group requests by hour (or day when range > 48 h) and count
        successful vs failed.

        Shape: [{"hour": "2026-06-24 14:00", "success": 5, "failed": 1}, ...]
        """
        # Determine date range to choose hour vs day bucketing
        timestamps: list[str] = [
            r.get("created_at") or "" for r in self.recent_requests if r.get("created_at")
        ]
        use_day = False
        if timestamps:
            try:
                oldest = min(timestamps)[:19]
                newest = max(timestamps)[:19]
                t_old = datetime.datetime.fromisoformat(oldest)
                t_new = datetime.datetime.fromisoformat(newest)
                diff_hours = (t_new - t_old).total_seconds() / 3600
                use_day = diff_hours > 48
            except Exception:
                # Malformed or non-ISO timestamps — fall back to hourly bucketing
                pass

        bucket_map: dict[str, dict[str, int]] = {}
        for r in self.recent_requests:
            raw_ts = r.get("created_at") or ""
            if not raw_ts:
                continue
            try:
                ts = datetime.datetime.fromisoformat(raw_ts[:19])
                if use_day:
                    bucket = ts.strftime("%Y-%m-%d")
                else:
                    bucket = ts.strftime("%Y-%m-%d %H:00")
            except Exception:
                bucket = raw_ts[:13]
            if bucket not in bucket_map:
                bucket_map[bucket] = {"success": 0, "partial": 0, "failed": 0}
            status = (r.get("status") or "").lower()
            if status in ("complete", "completed"):
                bucket_map[bucket]["success"] += 1
            elif status == "partial":
                # Partial = some instances succeeded; distinct from full failure.
                bucket_map[bucket]["partial"] += 1
            elif status in ("failed", "error", "timeout"):
                bucket_map[bucket]["failed"] += 1
        result: list[dict[str, Any]] = [
            {
                "hour": k,
                "success": v["success"],
                "partial": v["partial"],
                "failed": v["failed"],
            }
            for k, v in sorted(bucket_map.items())
        ]
        return result

    @rx.var
    def duration_scatter_data(self) -> list[dict[str, Any]]:
        """One dot per request: x = created_at epoch seconds, y = duration seconds.

        Shape: [{"x": 1750000000, "y": 42}, ...]

        Falls back to computing duration from ``completed_at - created_at``
        when the backend doesn't supply a precomputed ``duration`` field —
        the wire format currently has it as ``None`` for every request.
        """

        def _parse_iso(s: str) -> datetime.datetime | None:
            if not s:
                return None
            try:
                # ``fromisoformat`` handles ``+00:00`` and ``Z`` natively on
                # Python 3.11+; normalise to UTC-aware so arithmetic with
                # ``_epoch`` below never mixes naive + aware.
                dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                return dt
            except Exception:
                return None

        _epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
        result: list[dict[str, Any]] = []
        for r in self.recent_requests:
            dur: int | None = None
            raw_dur = r.get("duration")
            if raw_dur is not None:
                try:
                    dur = int(raw_dur)
                except (ValueError, TypeError):
                    dur = None
            if dur is None:
                start = _parse_iso(r.get("created_at") or "")
                end = _parse_iso(r.get("completed_at") or "")
                if start and end:
                    dur = max(0, int((end - start).total_seconds()))
            if dur is None:
                continue
            start = _parse_iso(r.get("created_at") or "")
            epoch = int((start - _epoch).total_seconds()) if start else 0
            result.append({"x": epoch, "y": dur})
        return result

    @rx.var
    def p95_duration(self) -> int:
        """95th-percentile duration in seconds across the loaded requests.

        Mirrors ``duration_scatter_data``'s fallback: if the backend hasn't
        populated ``duration``, compute it from completed_at - created_at.
        """

        def _parse_iso(s: str) -> datetime.datetime | None:
            if not s:
                return None
            try:
                dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                return dt
            except Exception:
                return None

        durations: list[int] = []
        for r in self.recent_requests:
            raw_dur = r.get("duration")
            if raw_dur is not None:
                try:
                    durations.append(int(raw_dur))
                    continue
                except (ValueError, TypeError):
                    # Non-numeric duration field — fall through to timestamp diff
                    pass
            start = _parse_iso(r.get("created_at") or "")
            end = _parse_iso(r.get("completed_at") or "")
            if start and end:
                durations.append(max(0, int((end - start).total_seconds())))
        if not durations:
            return 0
        durations.sort()
        idx = max(0, int(len(durations) * 0.95) - 1)
        return durations[idx]

    @rx.var
    def template_usage_data(self) -> list[dict[str, Any]]:
        """Aggregate requests by template_id: fulfilled vs failed instance counts.

        Uses all loaded requests (not capped at 50) so the chart reflects the
        full dataset.  Sorted descending by fulfilled, capped to top 8 entries.

        Shape: [{"template": "t-foo", "fulfilled": 18, "failed": 2, "requests": 5}, ...]
        """
        agg: dict[str, dict[str, int]] = defaultdict(
            lambda: {"fulfilled": 0, "failed": 0, "requests": 0}
        )
        for r in self.recent_requests:
            tid = str(r.get("template_id") or "")
            if not tid:
                continue
            agg[tid]["fulfilled"] += int(r.get("successful_count") or r.get("fulfilled_units") or 0)
            agg[tid]["failed"] += int(r.get("failed_count") or 0)
            agg[tid]["requests"] += 1
        items = [
            {
                "template": k,
                "fulfilled": v["fulfilled"],
                "failed": v["failed"],
                "requests": v["requests"],
            }
            for k, v in agg.items()
        ]
        items.sort(key=lambda x: x["fulfilled"], reverse=True)
        return items[:8]


# ----------------------------------------------------------------- components


def _stat_card(
    label: str,
    value: Any,
    accent: str = "blue",
    icon: str = "activity",
) -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.icon(icon, size=16, color=rx.color(accent, 9)),
                rx.text(label, size="2", color=rx.color("gray", 11)),
                spacing="2",
                align="center",
            ),
            rx.heading(value, size="7", color=rx.color(accent, 11)),
            spacing="2",
            align="start",
        ),
        padding="1rem 1.25rem",
        background=rx.color("gray", 1),
        border_radius="0.5rem",
        border=f"1px solid {rx.color('gray', 5)}",
        flex="1",
        min_width="120px",
    )


def _stat_group(
    title: str,
    icon: str,
    *cards: rx.Component,
    href: str = "",
    flex_grow: str = "1",
    min_width: str = "280px",
    card_columns: int | None = None,
) -> rx.Component:
    """Bordered card-group with a section heading.

    Order is workflow-narrative: Templates -> Requests -> Machines.
    A template defines the shape, a request asks for it, machines are
    the result.

    Each group is a flex item with its own flex_grow/min_width so:
      - Templates can be a narrow single-card tile.
      - Machines can be a wide 4-card row that never wraps internally.
      - Requests is the middle child.

    When ``href`` is supplied the header becomes a clickable link to the
    relevant page (Templates / Requests / Machines list views).
    ``card_columns`` forces a fixed-column rx.grid for the cards (machines
    uses 4 so they stay on one row); when None the cards lay out in a
    wrapping flex.
    """
    if card_columns is not None:
        cards_layout = rx.grid(
            *cards,
            columns=str(card_columns),
            gap="2",
            width="100%",
        )
    else:
        cards_layout = rx.flex(
            *cards,
            spacing="2",
            flex_wrap="wrap",
            width="100%",
        )

    title_component = rx.hstack(
        rx.icon(icon, size=14, color=rx.color("gray", 11)),
        rx.text(
            title,
            size="1",
            weight="bold",
            color=rx.color("gray", 11),
            text_transform="uppercase",
            letter_spacing="0.05em",
        ),
        rx.cond(
            href != "",
            rx.icon(
                "arrow-up-right",
                size=12,
                color=rx.color("gray", 9),
            ),
            rx.fragment(),
        ),
        spacing="2",
        align="center",
    )
    if href:
        title_component = rx.link(
            title_component,
            href=href,
            underline="none",
            _hover={"color": rx.color("blue", 11)},
        )

    return rx.box(
        rx.vstack(
            title_component,
            cards_layout,
            spacing="2",
            align="start",
            width="100%",
        ),
        padding="0.75rem 1rem 1rem",
        background=rx.color("gray", 2),
        border_radius="0.75rem",
        border=f"1px solid {rx.color('gray', 5)}",
        flex_grow=flex_grow,
        flex_shrink="1",
        flex_basis="0",
        min_width=min_width,
    )


def _inline_step(row: Any, field_key: str, label: str, color_name: str) -> rx.Component:
    """One marker in the inline lifecycle stepper. Coloured when timestamp
    present, dashed-gray when absent. Hover shows the timestamp."""
    present = row[field_key] != ""
    return rx.tooltip(
        rx.box(
            width="10px",
            height="10px",
            border_radius="50%",
            background=rx.cond(present, rx.color(color_name, 9), rx.color("gray", 5)),
            border=rx.cond(present, "none", f"1px dashed {rx.color('gray', 7)}"),
            flex_shrink="0",
        ),
        content=rx.Var.create(label + ": ") + row[field_key].to(str),
    )


def _inline_lifecycle(row: Any) -> rx.Component:
    """Compact inline lifecycle stepper rendered in the right-most cell of
    each activity row. 5 markers (Created → Started → First check → Last
    check → Completed) separated by short connector lines."""
    # Short fixed-width connectors keep the stepper compact; previously
    # ``flex_grow=1`` connectors stretched to fill the table cell, scattering
    # the dots across the full column width.
    connector = rx.box(
        height="1px",
        width="14px",
        background=rx.color("gray", 5),
        align_self="center",
        flex_shrink="0",
    )
    return rx.hstack(
        _inline_step(row, "created_at", "Created", "blue"),
        connector,
        _inline_step(row, "started_at", "Started", "amber"),
        connector,
        _inline_step(row, "first_status_check", "First check", "violet"),
        connector,
        _inline_step(row, "last_status_check", "Last check", "violet"),
        connector,
        _inline_step(row, "completed_at", "Completed", "green"),
        spacing="0",
        align="center",
    )


def _activity_row(row) -> rx.Component:
    """Render one pre-formatted activity row with inline lifecycle stepper.

    Cell widths use CSS ``width: auto`` everywhere except the Template
    column (flexible filler) and Lifecycle (right-anchored fixed width).
    Combined with ``table_layout="auto"`` on the table root, the browser
    distributes remaining horizontal space to Template while keeping the
    short fields (Time, Type, Status, Fulfilled) snug against their
    content. Lifecycle hugs the right edge with a fixed 200px width so
    it never collides with the Template overflow.
    """
    vc = DashboardState.visible_columns  # type: ignore[attr-defined]
    return rx.table.row(
        rx.cond(
            vc.contains(",time,"),
            rx.table.cell(
                rx.text(row["time"], size="1", color=rx.color("gray", 10), white_space="nowrap"),
                vertical_align="middle",
                white_space="nowrap",
            ),
            rx.fragment(),
        ),
        rx.cond(
            vc.contains(",request_id,"),
            rx.table.cell(
                rx.button(
                    rx.code(row["short_id"], size="1", white_space="nowrap"),
                    on_click=RequestsState.open_drawer(row["raw"]),
                    variant="ghost",
                    size="1",
                    cursor="pointer",
                ),
                vertical_align="middle",
                white_space="nowrap",
            ),
            rx.fragment(),
        ),
        rx.cond(
            vc.contains(",type,"),
            rx.table.cell(
                rx.text(row["type"], size="2"),
                vertical_align="middle",
                white_space="nowrap",
            ),
            rx.fragment(),
        ),
        rx.cond(
            vc.contains(",status,"),
            rx.table.cell(
                request_status_badge(row["status"]),
                vertical_align="middle",
                white_space="nowrap",
            ),
            rx.fragment(),
        ),
        rx.cond(
            vc.contains(",template_id,"),
            rx.table.cell(
                rx.text(
                    row["template_id"],
                    size="2",
                    color=rx.color("gray", 11),
                    overflow="hidden",
                    text_overflow="ellipsis",
                    white_space="nowrap",
                ),
                vertical_align="middle",
                # Flex filler — eats remaining horizontal space so the
                # lifecycle stepper sits flush against the right edge.
                width="100%",
                min_width="220px",
            ),
            rx.fragment(),
        ),
        rx.cond(
            vc.contains(",fulfilled,"),
            rx.table.cell(
                rx.text(row["fulfilled"], size="2", text_align="right"),
                vertical_align="middle",
                text_align="right",
                white_space="nowrap",
            ),
            rx.fragment(),
        ),
        rx.cond(
            vc.contains(",lifecycle,"),
            rx.table.cell(
                _inline_lifecycle(row),
                vertical_align="middle",
                white_space="nowrap",
                text_align="right",
            ),
            rx.fragment(),
        ),
        cursor="pointer",
        _hover={"background": rx.color("gray", 3)},
    )


def _activity_table() -> rx.Component:
    return rx.box(
        rx.vstack(
            # header row
            rx.hstack(
                rx.vstack(
                    rx.heading("Recent Activity", size="4"),
                    rx.text(
                        "Latest 15 requests with inline lifecycle stages. "
                        "Hover any marker for its exact timestamp.",
                        size="1",
                        color=rx.color("gray", 10),
                    ),
                    spacing="1",
                    align="start",
                ),
                rx.spacer(),
                column_picker(
                    columns=ACTIVITY_COLUMNS,
                    visible_columns=DashboardState.visible_columns,
                    on_toggle=DashboardState.toggle_column,
                ),
                rx.link(
                    rx.text("View all", size="2", color=rx.color("blue", 9)),
                    href="/requests",
                    underline="hover",
                ),
                width="100%",
                align="center",
                spacing="3",
            ),
            rx.cond(
                # Only show the spinner when we have nothing to display yet.
                # Once any rows arrive, swap to the table even if the
                # background load is still running on other endpoints.
                DashboardState.loading & ~DashboardState.has_activity,
                rx.center(
                    rx.spinner(size="3"),
                    padding="2rem",
                    width="100%",
                ),
                rx.cond(
                    DashboardState.has_activity,
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.cond(
                                    DashboardState.visible_columns.contains(",time,"),
                                    rx.table.column_header_cell("Time", white_space="nowrap"),
                                    rx.fragment(),
                                ),
                                rx.cond(
                                    DashboardState.visible_columns.contains(",request_id,"),
                                    rx.table.column_header_cell("Request ID", white_space="nowrap"),
                                    rx.fragment(),
                                ),
                                rx.cond(
                                    DashboardState.visible_columns.contains(",type,"),
                                    rx.table.column_header_cell("Type", white_space="nowrap"),
                                    rx.fragment(),
                                ),
                                rx.cond(
                                    DashboardState.visible_columns.contains(",status,"),
                                    rx.table.column_header_cell("Status", white_space="nowrap"),
                                    rx.fragment(),
                                ),
                                rx.cond(
                                    DashboardState.visible_columns.contains(",template_id,"),
                                    # Flex filler header — eats remaining width
                                    rx.table.column_header_cell(
                                        "Template", width="100%", min_width="220px"
                                    ),
                                    rx.fragment(),
                                ),
                                rx.cond(
                                    DashboardState.visible_columns.contains(",fulfilled,"),
                                    rx.table.column_header_cell(
                                        "Fulfilled", text_align="right", white_space="nowrap"
                                    ),
                                    rx.fragment(),
                                ),
                                rx.cond(
                                    DashboardState.visible_columns.contains(",lifecycle,"),
                                    rx.table.column_header_cell(
                                        "Lifecycle", white_space="nowrap", text_align="right"
                                    ),
                                    rx.fragment(),
                                ),
                            ),
                        ),
                        rx.table.body(
                            rx.foreach(
                                DashboardState.recent_activity,
                                _activity_row,
                            ),
                        ),
                        width="100%",
                        # No fixed min_width — let the browser auto-fit the
                        # table to whatever container width is available.
                        # Template column has width=100% so it absorbs the
                        # remaining space, pushing Lifecycle to the right edge.
                        style={"table_layout": "auto"},
                        variant="surface",
                    ),
                    empty_state(
                        icon="inbox",
                        title="No requests yet",
                        description="Machine requests will appear here once submitted.",
                    ),
                ),
            ),
            spacing="4",
            width="100%",
        ),
        padding="1.5rem",
        background=rx.color("gray", 2),
        border_radius="0.5rem",
        border=f"1px solid {rx.color('gray', 5)}",
        width="100%",
        overflow_x="auto",
    )


# ----------------------------------------------------------------- chart helpers


def _chart_card(
    title: str | rx.Component,
    body: rx.Component,
    full_width: bool = False,
) -> rx.Component:
    """Wraps a chart in a card with a heading."""
    return rx.card(
        rx.vstack(
            rx.heading(title, size="3", margin_bottom="0.5rem"),
            body,
            spacing="2",
            width="100%",
        ),
        width="100%" if full_width else None,
        min_height="300px",
        padding="1rem",
    )


def _donut_chart() -> rx.Component:
    return rx.recharts.pie_chart(
        rx.recharts.pie(
            data=DashboardState.status_donut_data,
            data_key="value",
            name_key="name",
            inner_radius="60%",
            outer_radius="90%",
            padding_angle=2,
            is_animation_active=False,
        ),
        rx.recharts.graphing_tooltip(
            content_style={
                "backgroundColor": "white",
                "color": "#111827",
                "border": "1px solid #d1d5db",
                "borderRadius": "6px",
                "padding": "8px 12px",
                "fontSize": "12px",
            },
            label_style={"color": "#111827", "fontWeight": "500"},
            item_style={"color": "#111827"},
        ),
        rx.recharts.legend(),
        width="100%",
        height=240,
    )


def _trend_bar_chart() -> rx.Component:
    return rx.recharts.bar_chart(
        rx.recharts.cartesian_grid(stroke_dasharray="3 3"),
        rx.recharts.x_axis(data_key="hour"),
        rx.recharts.y_axis(),
        rx.recharts.graphing_tooltip(
            content_style={
                "backgroundColor": "white",
                "color": "#111827",
                "border": "1px solid #d1d5db",
                "borderRadius": "6px",
                "padding": "8px 12px",
                "fontSize": "12px",
            },
            label_style={"color": "#111827", "fontWeight": "500"},
            item_style={"color": "#111827"},
        ),
        rx.recharts.legend(),
        rx.recharts.bar(
            data_key="success", fill="#22c55e", is_animation_active=False, name="Complete"
        ),
        rx.recharts.bar(
            data_key="partial", fill="#a78bfa", is_animation_active=False, name="Partial"
        ),
        rx.recharts.bar(
            data_key="failed", fill="#ef4444", is_animation_active=False, name="Failed"
        ),
        data=DashboardState.fulfillment_trend_data,
        width="100%",
        height=240,
    )


def _duration_scatter() -> rx.Component:
    return rx.recharts.scatter_chart(
        rx.recharts.cartesian_grid(),
        rx.recharts.x_axis(data_key="x", type_="number", name="time"),
        rx.recharts.y_axis(data_key="y", type_="number", name="duration (s)"),
        rx.recharts.graphing_tooltip(
            content_style={
                "backgroundColor": "white",
                "color": "#111827",
                "border": "1px solid #d1d5db",
                "borderRadius": "6px",
                "padding": "8px 12px",
                "fontSize": "12px",
            },
            label_style={"color": "#111827", "fontWeight": "500"},
            item_style={"color": "#111827"},
        ),
        rx.recharts.scatter(
            data=DashboardState.duration_scatter_data,
            fill="#3b82f6",
            is_animation_active=False,
        ),
        width="100%",
        height=240,
    )


def _template_bar_chart() -> rx.Component:
    return rx.recharts.bar_chart(
        rx.recharts.cartesian_grid(stroke_dasharray="3 3"),
        rx.recharts.x_axis(type_="number"),
        rx.recharts.y_axis(
            data_key="template",
            type_="category",
            # Template names are user-supplied and often long
            # ("aws-c5n-9xlarge-ondemand-us-east-1"). 140px truncated
            # at ~14 chars.  Bump width to 240px so the full name fits
            # at the default 12px font; recharts doesn't support
            # dynamic-per-label sizing so this is the only knob.
            width=240,
            tick_line=False,
        ),
        rx.recharts.graphing_tooltip(
            content_style={
                "backgroundColor": "white",
                "color": "#111827",
                "border": "1px solid #d1d5db",
                "borderRadius": "6px",
                "padding": "8px 12px",
                "fontSize": "12px",
            },
            label_style={"color": "#111827", "fontWeight": "500"},
            item_style={"color": "#111827"},
        ),
        rx.recharts.legend(),
        rx.recharts.bar(
            data_key="fulfilled",
            fill="#22c55e",
            stack_id="a",
            is_animation_active=False,
            name="Provisioned",
        ),
        rx.recharts.bar(
            data_key="failed",
            fill="#ef4444",
            stack_id="a",
            is_animation_active=False,
            name="Failed",
        ),
        data=DashboardState.template_usage_data,
        layout="vertical",
        width="100%",
        height=240,
    )


def _dot(color: str, label: str) -> rx.Component:
    """Static helper — color/label are Python literals, not Vars."""
    return rx.tooltip(
        rx.box(
            width="10px",
            height="10px",
            border_radius="50%",
            background=color,
            flex_shrink="0",
        ),
        content=label,
    )


# ----------------------------------------------------------------- page


def dashboard_page() -> rx.Component:
    # Build the p95 duration label for the scatter chart title
    p95_label = rx.hstack(
        rx.text("Duration vs Time", size="3"),
        rx.badge(
            "p95: " + DashboardState.p95_duration.to_string() + "s",
            variant="soft",
            color_scheme="blue",
            size="1",
        ),
        spacing="2",
        align="center",
    )

    return page(
        "Dashboard",
        # Error banner
        rx.cond(
            DashboardState.error != "",
            error_callout(DashboardState.error),
            rx.fragment(),
        ),
        # Onboarding CTA — shown only when no templates exist and the user
        # hasn't dismissed it.  Persistence via AppState.onboarding_dismissed
        # LocalStorage bool so the flag survives reload but stays per-browser
        # (no server-side write path needed).
        rx.cond(
            (DashboardState.total_templates == 0)
            & ~DashboardState.loading
            & ~AppState.onboarding_dismissed,
            rx.box(
                rx.vstack(
                    rx.hstack(
                        rx.icon("rocket", size=20, color=rx.color("blue", 9)),
                        rx.heading("Get started with ORB", size="4"),
                        rx.spacer(),
                        rx.icon_button(
                            rx.icon("x", size=14),
                            on_click=AppState.dismiss_onboarding,
                            variant="ghost",
                            color_scheme="gray",
                            size="1",
                            aria_label="Dismiss onboarding",
                        ),
                        spacing="2",
                        align="center",
                        width="100%",
                    ),
                    rx.text(
                        "No templates found. Create a machine template to start "
                        "provisioning resources.",
                        size="2",
                        color=rx.color("gray", 11),
                    ),
                    rx.hstack(
                        rx.link(
                            rx.button(
                                rx.icon("file-plus", size=14),
                                "Create Template",
                                color_scheme="blue",
                                size="2",
                            ),
                            href="/templates",
                            underline="none",
                        ),
                        rx.link(
                            rx.button(
                                rx.icon("book-open", size=14),
                                "View Docs",
                                variant="soft",
                                color_scheme="gray",
                                size="2",
                            ),
                            href="https://github.com/orb-project/orb",
                            is_external=True,
                            underline="none",
                        ),
                        spacing="3",
                        align="center",
                    ),
                    spacing="3",
                    align="start",
                ),
                padding="1.5rem",
                background=rx.color("blue", 2),
                border_radius="0.5rem",
                border=f"1px solid {rx.color('blue', 5)}",
                width="100%",
                margin_bottom="0.5rem",
            ),
            rx.fragment(),
        ),
        # Refresh row — auto-refresh control on the right, above the
        # stat cards so it's discoverable without scrolling past the
        # counts.  Mirrors the pattern on the list pages where the
        # refresh_control sits in the top-of-page filter/toolbar row.
        rx.hstack(
            rx.spacer(),
            refresh_control(
                enabled=DashboardState.auto_refresh_enabled,
                interval=DashboardState.auto_refresh_interval,
                on_toggle=DashboardState.toggle_auto_refresh,
                on_set_interval=DashboardState.set_auto_refresh_interval,
                on_manual_refresh=DashboardState.load,
                last_refresh_text=DashboardState.last_refresh,
                loading=DashboardState.loading,
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        # Stat cards — grouped by resource type and ordered by workflow
        # direction: Templates define the shape → Requests ask for the
        # shape → Machines are the result. Each tile is clickable and
        # routes to the matching list page.
        rx.flex(
            _stat_group(
                "Templates",
                "file-text",
                _stat_card("Total", DashboardState.total_templates, "purple", "file-text"),
                href="/templates",
                flex_grow="0",
                min_width="180px",
            ),
            _stat_group(
                "Requests",
                "list-checks",
                _stat_card("In Flight", DashboardState.in_flight_requests, "blue", "loader"),
                _stat_card("Completed", DashboardState.completed_requests, "green", "check"),
                _stat_card("Failed", DashboardState.failed_requests, "red", "triangle-alert"),
                href="/requests",
                flex_grow="2",
                min_width="380px",
                card_columns=3,
            ),
            _stat_group(
                "Machines",
                "server",
                _stat_card("Total", DashboardState.total_machines, "blue", "server"),
                _stat_card("Running", DashboardState.running_machines, "green", "play"),
                _stat_card("Pending", DashboardState.pending_machines, "blue", "clock"),
                _stat_card("Terminated", DashboardState.terminated_machines, "gray", "x-circle"),
                href="/machines",
                flex_grow="3",
                min_width="520px",
                card_columns=4,
            ),
            spacing="4",
            flex_wrap="wrap",
            width="100%",
            align_items="stretch",
        ),
        # Activity Insights — chart section
        rx.heading("Activity Insights", size="4", margin_top="1rem", margin_bottom="0.75rem"),
        rx.grid(
            _chart_card("Request Status", _donut_chart()),
            _chart_card("Fulfillment Trend", _trend_bar_chart()),
            _chart_card(p95_label, _duration_scatter()),
            _chart_card(
                "Template Performance (provisioned vs failed instances)", _template_bar_chart()
            ),
            # Fixed 2-column grid on desktop; collapses to 1 column under
            # 768px. ``auto-fit minmax(360px,1fr)`` was producing a 3-up row
            # at desktop widths (4 charts × ~360px > viewport), forcing the
            # 4th chart onto its own row alone.
            columns=rx.breakpoints(initial="1", md="2"),
            gap="1rem",
            width="100%",
        ),
        rx.box(height="0.75rem"),
        # Combined activity feed — columnar data + inline lifecycle stepper
        # per row (previously two separate tables).
        _activity_table(),
        # Drawers mounted inline so clicking a request-id in the activity
        # table opens the same detail panel the requests page uses — no
        # cross-page navigation needed. Quick-view machine drawer mounted
        # alongside so machine drill-in from the request drawer works too.
        request_drawer(RequestsState),
        machine_drawer(MachineQuickViewState),
        on_mount=[DashboardState.load, DashboardState.auto_refresh],
    )
