"""Request detail drawer.

Reads from RequestsState.drawer_view — a single Vars-safe dict computed in
state with all string fallbacks, terminal flags, and progress percent
pre-resolved. Templates here use only Reflex-native operations.

Live polling is driven by RequestsState.poll_drawer_progress (background
task, 2-s interval) rather than SSE EventSource, which fights Reflex's
WebSocket transport.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import reflex as rx

from .drawer_utils import drawer_section as _drawer_section
from .machine_quick_view import MachineQuickViewState as _MachineQuickViewState
from .request_modal import RequestModalState as _RequestModalState
from .status_badge import request_status_badge as _request_status_badge

if TYPE_CHECKING:
    pass


def _section(title: str, *children: rx.Component) -> rx.Component:
    """Boxed section — delegates to the shared drawer_section helper.

    Uses ``boxed=True`` so the request drawer keeps its bordered-card
    style while staying consistent with machine_drawer / template_drawer.
    """
    return _drawer_section(title, *children, boxed=True)


def _kv(label: str, value) -> rx.Component:
    return rx.hstack(
        rx.text(label, size="1", color=rx.color("gray", 11), min_width="120px"),
        rx.cond(
            isinstance(value, str),
            rx.text(value, size="2") if isinstance(value, str) else rx.fragment(),
            value if not isinstance(value, str) else rx.fragment(),
        ),
        spacing="3",
        align="start",
        width="100%",
        mb="1",
    )


def _kv_text(label: str, value) -> rx.Component:
    return rx.hstack(
        rx.text(label, size="1", color=rx.color("gray", 11), min_width="120px"),
        rx.text(value, size="2"),
        spacing="3",
        align="start",
        width="100%",
        mb="1",
    )


def _machine_row(m) -> rx.Component:
    """Single machine reference row inside the drawer. ``m`` is a Var dict.

    Plain rx.box (NOT rx.button) so the row inherits the section's
    content width exactly — Radix button chrome (min-height, native
    padding heuristics, border-radius growing the box-shadow on hover)
    was making the hover background spill past the section boundary.

    Cells: status badge + machine_id + instance_type + private_ip +
    chevron. Long IDs/IPs truncate via min_width=0 + overflow ellipsis
    on the flex child that's allowed to shrink (machine_id). The
    chevron + badge + small text fields stay flex_shrink=0 so they
    keep their full width while machine_id absorbs the squeeze.
    """
    return rx.box(
        rx.hstack(
            rx.badge(
                m["status"],
                color_scheme=rx.match(
                    m["status"],
                    ("running", "green"),
                    ("succeed", "green"),
                    ("success", "green"),
                    ("failed", "red"),
                    ("error", "red"),
                    ("terminated", "gray"),
                    "blue",
                ),
                variant="soft",
                size="1",
                flex_shrink="0",
            ),
            rx.code(
                m["machine_id"],
                size="1",
                white_space="nowrap",
                overflow="hidden",
                text_overflow="ellipsis",
                min_width="0",
                flex_shrink="1",
                style={"flex": "1 1 auto"},
            ),
            rx.text(
                m["instance_type"],
                size="1",
                color=rx.color("gray", 11),
                white_space="nowrap",
                flex_shrink="0",
            ),
            rx.text(
                m["private_ip_address"],
                size="1",
                color=rx.color("gray", 10),
                white_space="nowrap",
                flex_shrink="0",
                font_family="monospace",
            ),
            rx.icon("chevron-right", size=14, color=rx.color("gray", 9), flex_shrink="0"),
            spacing="3",
            align="center",
            width="100%",
        ),
        on_click=_MachineQuickViewState.open_drawer(m),
        title="Open machine detail",
        role="button",
        tab_index=0,
        cursor="pointer",
        width="100%",
        padding="0.4rem 0.5rem",
        border_radius="0.25rem",
        border_bottom=f"1px solid {rx.color('gray', 4)}",
        _hover={"background": rx.color("gray", 3)},
        _last_child={"border_bottom": "none"},
        style={"box_sizing": "border-box"},
    )


_pulsing_dot = rx.box(
    width="6px",
    height="6px",
    border_radius="50%",
    background=rx.color("green", 9),
    style={"animation": "pulse 2s ease-in-out infinite"},
)


def _live_indicator(state: type) -> rx.Component:
    """Pulsing green dot + 'live' label shown while request is non-terminal."""
    return rx.cond(
        ~state.selected_request_is_terminal,
        rx.hstack(
            _pulsing_dot,
            rx.text("live", size="1", color=rx.color("green", 11)),
            spacing="1",
            align="center",
        ),
        rx.fragment(),
    )


def _live_poll_control(state: type) -> rx.Component:
    """Compact live-poll toggle: icon-only checkbox + pulsing dot when active.

    Dropped the "Live updates" label — too wide for the drawer header on
    narrow viewports, was wrapping to a second line. The activity-pulse dot
    next to the checkbox is enough signal; tooltip carries the wording.
    """
    # Match the height of an ``rx.button(size="2")`` (~32px) so the checkbox
    # row baselines with the neighbouring Sync button rather than top-aligning.
    #
    # Tooltip wraps only the non-focusable icon. Wrapping the focusable
    # checkbox makes Radix auto-open the tooltip when the dialog grabs
    # initial focus on mount — leaving it stuck open until the user clicks
    # elsewhere. Native ``title=`` attr on the checkbox gives keyboard /
    # screen-reader users equivalent text.
    return rx.hstack(
        rx.checkbox(
            checked=state.live_poll_enabled == "true",
            on_change=state.toggle_live_poll,
            size="1",
            title="Live updates — auto-refresh every 2s while open",
        ),
        rx.cond(
            (state.live_poll_enabled == "true") & ~state.selected_request_is_terminal,
            rx.tooltip(_pulsing_dot, content="Live updates — auto-refresh every 2s while open"),
            rx.tooltip(
                rx.icon("radio", size=14, color=rx.color("gray", 9)),
                content="Live updates — auto-refresh every 2s while open",
            ),
        ),
        spacing="1",
        align="center",
        display="inline-flex",
        height="32px",
        padding_x="0.25rem",
    )


def _timeline_event_row(event: dict) -> rx.Component:
    """Single timeline row: colored circle + label + timestamp.

    ``event`` has keys: label (str), ts (str), color (str), present (str "1"/"0").
    """
    circle_bg = rx.match(
        event["color"],
        ("blue", rx.color("blue", 9)),
        ("amber", rx.color("amber", 9)),
        ("violet", rx.color("violet", 9)),
        ("green", rx.color("green", 9)),
        ("red", rx.color("red", 9)),
        rx.color("gray", 6),
    )
    return rx.hstack(
        # Timeline dot
        rx.box(
            width="10px",
            height="10px",
            border_radius="50%",
            background=circle_bg,
            flex_shrink="0",
        ),
        # Label
        rx.text(
            event["label"],
            size="1",
            color=rx.cond(event["present"] == "1", rx.color("gray", 12), rx.color("gray", 9)),
            min_width="160px",
        ),
        # Timestamp or dash placeholder
        rx.cond(
            event["present"] == "1",
            rx.code(event["ts"], size="1"),
            rx.text("—", size="1", color=rx.color("gray", 8)),
        ),
        spacing="2",
        align="center",
        width="100%",
        padding_y="2px",
    )


_PULSE_KEYFRAMES = """
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.85); }
}
"""


def request_drawer(state: type) -> rx.Component:
    """Drawer that shows the full detail of `state.selected_request`.

    Consumes per-field typed Vars from the state class (NOT a single
    `dict[str, Any]` blob — those collapse to AnyVar at compile time).
    """
    return rx.dialog.root(
        rx.el.style(_PULSE_KEYFRAMES),
        rx.dialog.content(
            # Header
            rx.hstack(
                rx.heading("Request Detail", size="5"),
                rx.spacer(),
                _live_poll_control(state),
                rx.button(
                    rx.icon("cloud-download", size=14),
                    "Sync",
                    variant="soft",
                    size="2",
                    loading=state.syncing_drawer,
                    on_click=state.refresh_drawer,
                    title="Pull live state from the cloud provider for this request",
                ),
                rx.icon_button(
                    rx.icon("x", size=16),
                    on_click=state.close_drawer,
                    variant="ghost",
                    size="2",
                    aria_label="Close drawer",
                ),
                align="center",
                padding="0.5rem 0",
                border_bottom=f"1px solid {rx.color('gray', 5)}",
                width="100%",
                margin_bottom="0.5rem",
                spacing="2",
            ),
            rx.cond(
                state.sync_error != "",
                rx.callout(
                    state.sync_error,
                    icon="triangle-alert",
                    color_scheme="red",
                    size="1",
                    margin_bottom="0.5rem",
                ),
                rx.fragment(),
            ),
            rx.cond(
                state.last_sync_time != "",
                rx.text(
                    "Last sync at " + state.last_sync_time,
                    size="1",
                    color=rx.color("gray", 11),
                    margin_bottom="0.5rem",
                ),
                rx.fragment(),
            ),
            # Body
            rx.vstack(
                _section(
                    "Identity",
                    rx.hstack(
                        rx.text(
                            "Request ID", size="1", color=rx.color("gray", 11), min_width="120px"
                        ),
                        rx.code(state.selected_request_id, size="1"),
                        spacing="3",
                        align="start",
                        width="100%",
                        mb="1",
                    ),
                    _kv_text("Type", state.selected_request_type),
                    rx.hstack(
                        rx.text("Status", size="1", color=rx.color("gray", 11), min_width="120px"),
                        _request_status_badge(state.selected_request_status),
                        _live_indicator(state),
                        spacing="3",
                        align="center",
                        width="100%",
                        mb="1",
                    ),
                ),
                _section(
                    "Fulfillment",
                    _kv_text("Template", state.selected_request_template),
                    rx.cond(
                        state.selected_request_is_weighted,
                        _kv_text(
                            "Requested",
                            state.selected_request_requested_count.to_string() + " units",
                        ),
                        _kv_text("Requested", state.selected_request_requested_count),
                    ),
                    rx.cond(
                        state.selected_request_is_weighted,
                        _kv_text(
                            "Fulfilled",
                            state.selected_request_display_units_fulfilled.to_string()
                            + " units ("
                            + state.selected_request_successful_count.to_string()
                            + " instances)",
                        ),
                        _kv_text("Fulfilled", state.selected_request_successful_count),
                    ),
                    rx.cond(
                        state.selected_request_is_weighted,
                        _kv_text("Target Units", state.selected_request_display_units_target),
                        rx.fragment(),
                    ),
                    rx.cond(
                        state.selected_request_is_return,
                        _kv_text("Returned", state.selected_request_returned_count),
                        rx.fragment(),
                    ),
                    rx.cond(
                        state.selected_request_has_instance_breakdown,
                        _kv_text("Instance Types", state.selected_request_instance_types_breakdown),
                        rx.fragment(),
                    ),
                    rx.cond(
                        state.selected_request_show_progress,
                        rx.vstack(
                            rx.cond(
                                state.selected_request_is_weighted,
                                rx.hstack(
                                    rx.text(
                                        "Progress",
                                        size="1",
                                        color=rx.color("gray", 11),
                                        min_width="120px",
                                    ),
                                    rx.text(
                                        state.selected_request_display_units_fulfilled,
                                        size="1",
                                        color=rx.color("gray", 11),
                                    ),
                                    rx.text("/", size="1", color=rx.color("gray", 11)),
                                    rx.text(
                                        rx.cond(
                                            state.selected_request_target_units > 0,
                                            state.selected_request_target_units,
                                            state.selected_request_requested_count,
                                        ),
                                        size="1",
                                        color=rx.color("gray", 11),
                                    ),
                                    rx.text("units", size="1", color=rx.color("gray", 10)),
                                    spacing="2",
                                    width="100%",
                                    mb="1",
                                ),
                                rx.hstack(
                                    rx.text(
                                        "Progress",
                                        size="1",
                                        color=rx.color("gray", 11),
                                        min_width="120px",
                                    ),
                                    rx.text(
                                        state.selected_request_successful_count,
                                        size="1",
                                        color=rx.color("gray", 11),
                                    ),
                                    rx.text("/", size="1", color=rx.color("gray", 11)),
                                    rx.text(
                                        state.selected_request_requested_count,
                                        size="1",
                                        color=rx.color("gray", 11),
                                    ),
                                    spacing="2",
                                    width="100%",
                                    mb="1",
                                ),
                            ),
                            rx.box(
                                rx.box(
                                    height="0.375rem",
                                    border_radius="full",
                                    background=rx.match(
                                        state.selected_request_status,
                                        ("failed", rx.color("red", 9)),
                                        ("complete", rx.color("green", 9)),
                                        ("completed", rx.color("green", 9)),
                                        rx.color("amber", 9),
                                    ),
                                    width=state.selected_request_progress_pct_weighted.to_string()
                                    + "%",
                                ),
                                height="0.375rem",
                                border_radius="full",
                                background=rx.color("gray", 4),
                                width="100%",
                                overflow="hidden",
                                role="progressbar",
                                aria_valuenow=state.selected_request_progress_pct_weighted,
                                aria_valuemin=0,
                                aria_valuemax=100,
                                aria_label="Request fulfillment progress",
                            ),
                            width="100%",
                            spacing="1",
                        ),
                        rx.fragment(),
                    ),
                ),
                # Single "Timeline" section — the structured event-row list
                # subsumes the old plain-kv "Timing" section (created /
                # last_checked / completed) and adds started + first_check
                # with status-coloured dots.
                _section(
                    "Timeline",
                    rx.vstack(
                        rx.foreach(
                            state.selected_request_timeline,
                            _timeline_event_row,
                        ),
                        spacing="1",
                        width="100%",
                    ),
                ),
                rx.cond(
                    state.selected_request_has_machines
                    | (state.selected_request_requested_count > 0),
                    _section(
                        "Insights",
                        rx.grid(
                            rx.box(
                                rx.text(
                                    "Capacity",
                                    size="2",
                                    weight="medium",
                                    margin_bottom="0.25rem",
                                ),
                                rx.recharts.pie_chart(
                                    rx.recharts.pie(
                                        data=state.selected_request_capacity_chart_data,
                                        data_key="value",
                                        name_key="name",
                                        inner_radius="55%",
                                        outer_radius="85%",
                                        padding_angle=2,
                                        is_animation_active=False,
                                    ),
                                    rx.recharts.graphing_tooltip(),
                                    rx.recharts.legend(),
                                    width="100%",
                                    height=180,
                                ),
                            ),
                            rx.box(
                                rx.text(
                                    "Machines by Status",
                                    size="2",
                                    weight="medium",
                                    margin_bottom="0.25rem",
                                ),
                                rx.recharts.bar_chart(
                                    rx.recharts.x_axis(data_key="status"),
                                    rx.recharts.y_axis(),
                                    rx.recharts.graphing_tooltip(),
                                    rx.recharts.bar(
                                        data_key="count",
                                        fill="#3b82f6",
                                        is_animation_active=False,
                                    ),
                                    data=state.selected_request_machine_status_data,
                                    width="100%",
                                    height=180,
                                ),
                            ),
                            columns="2",
                            gap="1rem",
                            width="100%",
                        ),
                    ),
                    rx.fragment(),
                ),
                rx.cond(
                    state.selected_request_is_failure_like,
                    _section(
                        "Error",
                        rx.accordion.root(
                            rx.accordion.item(
                                header="Error Details",
                                content=rx.cond(
                                    state.selected_request_has_message,
                                    rx.callout(
                                        state.selected_request_message,
                                        icon="triangle-alert",
                                        color_scheme="red",
                                        size="1",
                                    ),
                                    rx.text(
                                        "No error message available. See Provider Data below.",
                                        size="2",
                                        color=rx.color("gray", 11),
                                    ),
                                ),
                                value="error_details",
                            ),
                            default_value="error_details",
                            width="100%",
                        ),
                    ),
                    rx.fragment(),
                ),
                rx.cond(
                    state.selected_request_has_machines,
                    _section(
                        "Machines",
                        rx.vstack(
                            rx.foreach(state.drawer_machines, _machine_row),
                            spacing="0",
                            width="100%",
                        ),
                    ),
                    rx.fragment(),
                ),
                rx.accordion.root(
                    rx.accordion.item(
                        header="Caller Metadata",
                        content=rx.code(
                            state.selected_request_metadata_str,
                            size="1",
                            style={
                                "white_space": "pre-wrap",
                                "font_size": "0.7rem",
                                "display": "block",
                            },
                        ),
                        value="meta",
                    ),
                    rx.accordion.item(
                        header="Provider Data",
                        content=rx.code(
                            state.selected_request_provider_data_str,
                            size="1",
                            style={
                                "white_space": "pre-wrap",
                                "font_size": "0.7rem",
                                "display": "block",
                            },
                        ),
                        value="provider_data",
                    ),
                    rx.accordion.item(
                        header="Raw JSON (full request row)",
                        content=rx.code(
                            state.selected_request_raw_json,
                            size="1",
                            style={
                                "white_space": "pre-wrap",
                                "font_size": "0.7rem",
                                "display": "block",
                            },
                        ),
                        value="raw",
                    ),
                    collapsible=True,
                    type="multiple",
                    variant="ghost",
                    width="100%",
                ),
                spacing="3",
                width="100%",
                align="start",
                max_height="60vh",
                overflow_y="auto",
            ),
            # Footer
            rx.divider(margin_top="1rem", margin_bottom="0.75rem"),
            rx.hstack(
                rx.cond(
                    ~state.selected_request_is_terminal,
                    rx.button(
                        "Cancel Request",
                        color_scheme="red",
                        variant="soft",
                        size="2",
                        on_click=state.confirm_cancel,
                    ),
                    rx.fragment(),
                ),
                rx.cond(
                    (state.selected_request_template != "")
                    & (state.selected_request_template != "—"),
                    rx.button(
                        rx.icon("send", size=14),
                        "Run again",
                        variant="outline",
                        size="2",
                        on_click=_RequestModalState.open_for(state.selected_request_template),
                        title="Submit a new request from the same template",
                    ),
                    rx.fragment(),
                ),
                rx.spacer(),
                rx.dialog.close(
                    rx.button(
                        "Close",
                        variant="soft",
                        color_scheme="gray",
                        size="2",
                        on_click=state.close_drawer,
                    )
                ),
                spacing="2",
                width="100%",
                align="center",
            ),
            max_width="640px",
        ),
        open=state.drawer_open,
        # CRITICAL: without ``on_open_change`` Radix dialogs become "controlled
        # but unacknowledged" — ESC / backdrop dismissals close the dialog
        # client-side without notifying the server, leaving ``drawer_open=True``
        # stale on the State. Next click sees ``True → True`` (no diff, no
        # render). Wiring this back keeps the State in sync with the actual
        # dialog state so subsequent opens always fire a clean False → True
        # transition.
        on_open_change=state.set_drawer_open,
    )
