"""Machine detail drawer — side panel showing full MachineDTO fields.

Rendered as a Radix Dialog (full-screen overlay) because Reflex's rx.drawer
component availability varies by version. The dialog acts as a slide-in panel
via CSS positioning constraints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import reflex as rx

from .drawer_utils import drawer_section as _drawer_section
from .request_modal import RequestModalState as _RequestModalState
from .status_badge import machine_status_badge as _machine_status_badge

if TYPE_CHECKING:
    pass


def _field_row(label: str, value: Any) -> rx.Component:
    """A label/value pair displayed as a compact 2-column row."""
    return rx.hstack(
        rx.text(
            label,
            size="2",
            color=rx.color("gray", 11),
            min_width="9rem",
            flex_shrink="0",
        ),
        rx.text(value, size="2", word_break="break-all"),
        spacing="3",
        align="start",
        width="100%",
    )


def _section(title: str, *rows: rx.Component) -> rx.Component:
    """A titled group of field rows.

    Delegates to the shared ``drawer_section()`` helper so all drawers
    use the same implementation.
    """
    return _drawer_section(title, *rows)


def _json_box(value: str) -> rx.Component:
    """A scrollable pre-formatted box for JSON-like text."""
    return rx.box(
        rx.code(
            value,
            size="1",
            white_space="pre-wrap",
            word_break="break-all",
        ),
        max_height="12rem",
        overflow_y="auto",
        background=rx.color("gray", 2),
        border_radius="0.375rem",
        padding="0.75rem",
        width="100%",
        border=f"1px solid {rx.color('gray', 5)}",
    )


def machine_drawer(state: type) -> rx.Component:
    """Return a modal dialog that acts as a machine detail drawer.

    Args:
        state: The MachinesState class (passed as type to avoid circular import).

    Returns:
        A Radix dialog component controlled by state.drawer_open.
    """
    m = state.selected_machine

    return rx.dialog.root(
        rx.dialog.content(
            # Header
            rx.hstack(
                rx.vstack(
                    rx.heading(
                        rx.cond(m["name"] != "", m["name"], m["machine_id"]),
                        size="5",
                    ),
                    rx.hstack(
                        rx.code(m["machine_id"], size="1"),
                        _machine_status_badge(m["status"]),
                        spacing="2",
                        align="center",
                    ),
                    spacing="1",
                    align="start",
                ),
                rx.spacer(),
                # Live-poll control. Tooltip wraps the non-focusable icon
                # only, NOT the checkbox — wrapping the focusable checkbox
                # causes Radix to auto-open the tooltip when the dialog
                # auto-focuses it on mount.
                rx.hstack(
                    rx.checkbox(
                        checked=state.live_poll_enabled == "true",
                        on_change=state.toggle_live_poll,
                        size="1",
                        title="Live updates — auto-refresh every 3s while open",
                    ),
                    rx.tooltip(
                        rx.icon("radio", size=14, color=rx.color("gray", 9)),
                        content="Live updates — auto-refresh every 3s while open",
                    ),
                    spacing="1",
                    align="center",
                    display="inline-flex",
                    height="32px",
                    padding_x="0.25rem",
                ),
                rx.button(
                    rx.icon("cloud-download", size=14),
                    "Sync",
                    variant="soft",
                    size="2",
                    loading=state.syncing_drawer,
                    on_click=state.sync_drawer_machine,
                    title="Pull live state from the cloud provider for this machine",
                ),
                rx.dialog.close(
                    rx.button(
                        rx.icon("x", size=16),
                        variant="ghost",
                        size="2",
                        on_click=state.close_drawer,
                        aria_label="Close",
                    )
                ),
                width="100%",
                align="start",
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
            rx.divider(margin_bottom="1.25rem"),
            # Scrollable content area
            rx.box(
                # Identity group
                _section(
                    "Identity",
                    _field_row("Machine ID", m["machine_id"]),
                    _field_row("Name", m["name"]),
                    _field_row("Instance Type", m["instance_type"]),
                    _field_row("Status", m["status"]),
                    _field_row("Status Reason", m["status_reason"]),
                    _field_row("Version", m["version"]),
                ),
                # Networking group
                _section(
                    "Networking",
                    _field_row("Private IP", m["private_ip"]),
                    _field_row("Public IP", m["public_ip"]),
                    _field_row("Private DNS", m["private_dns_name"]),
                    _field_row("Public DNS", m["public_dns_name"]),
                    _field_row("Subnet ID", m["subnet_id"]),
                    _field_row("Security Groups", state.selected_machine_sg_text),
                ),
                # Provider group
                _section(
                    "Provider",
                    _field_row("Provider Name", m["provider_name"]),
                    _field_row("Provider Type", m["provider_type"]),
                    _field_row("Provider API", m["provider_api"]),
                    _field_row("Resource ID", m["resource_id"]),
                    _field_row("Cloud Host ID", m["cloud_host_id"]),
                    _field_row("Region", m["region"]),
                    _field_row("Availability Zone", m["availability_zone"]),
                    _field_row("vCPUs", m["vcpus"]),
                    _field_row("Price Type", m["price_type"]),
                ),
                # Metadata group
                _section(
                    "Metadata",
                    _field_row("Launch Time", state.selected_machine_launch_fmt),
                    _field_row("Termination Time", state.selected_machine_term_fmt),
                    _field_row("Request ID", m["request_id"]),
                    _field_row("Return Request ID", m["return_request_id"]),
                    _field_row("Template ID", m["template_id"]),
                    _field_row("Image ID", m["image_id"]),
                    _field_row("Result", m["result"]),
                    _field_row("Message", m["message"]),
                ),
                # Tags group
                rx.vstack(
                    rx.text("Tags", size="3", weight="bold", color=rx.color("gray", 12)),
                    rx.divider(),
                    rx.cond(
                        state.selected_machine_tags_text != "{}",
                        _json_box(state.selected_machine_tags_text),
                        rx.text("No tags", size="2", color=rx.color("gray", 11)),
                    ),
                    spacing="2",
                    align="start",
                    width="100%",
                    padding_bottom="1rem",
                ),
                # Health Checks group
                rx.vstack(
                    rx.text("Health Checks", size="3", weight="bold", color=rx.color("gray", 12)),
                    rx.divider(),
                    rx.cond(
                        state.selected_machine_health_text != "null",
                        _json_box(state.selected_machine_health_text),
                        rx.text("No health check data", size="2", color=rx.color("gray", 11)),
                    ),
                    spacing="2",
                    align="start",
                    width="100%",
                    padding_bottom="1rem",
                ),
                # Provider Data group
                rx.vstack(
                    rx.text(
                        "Provider Data",
                        size="3",
                        weight="bold",
                        color=rx.color("gray", 12),
                    ),
                    rx.divider(),
                    rx.cond(
                        state.selected_machine_provider_data_text != "{}",
                        _json_box(state.selected_machine_provider_data_text),
                        rx.text("No provider data", size="2", color=rx.color("gray", 11)),
                    ),
                    spacing="2",
                    align="start",
                    width="100%",
                    padding_bottom="1rem",
                ),
                overflow_y="auto",
                max_height="calc(100vh - 16rem)",
                padding_right="0.25rem",
            ),
            # Footer actions
            rx.divider(margin_top="1rem", margin_bottom="0.75rem"),
            rx.hstack(
                rx.cond(
                    (m["status"] == "pending")
                    | (m["status"] == "running")
                    | (m["status"] == "stopped"),
                    rx.alert_dialog.root(
                        rx.alert_dialog.trigger(
                            rx.button(
                                rx.icon("log-out", size=14),
                                "Return Machine",
                                color_scheme="red",
                                variant="soft",
                                size="2",
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
                                    rx.code(m["machine_id"], size="1"),
                                    spacing="2",
                                )
                            ),
                            rx.hstack(
                                rx.alert_dialog.cancel(
                                    rx.button("Cancel", variant="soft", color_scheme="gray"),
                                ),
                                rx.alert_dialog.action(
                                    rx.button(
                                        "Return Machine",
                                        color_scheme="red",
                                        on_click=state.return_drawer_machine,
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
                rx.cond(
                    m["template_id"] != "",
                    rx.button(
                        rx.icon("send", size=14),
                        "Re-request",
                        variant="outline",
                        size="2",
                        on_click=_RequestModalState.open_for(m["template_id"]),
                        title="Request a new machine from the same template",
                    ),
                    rx.fragment(),
                ),
                rx.spacer(),
                rx.dialog.close(
                    rx.button(
                        "Close",
                        variant="soft",
                        color_scheme="gray",
                        on_click=state.close_drawer,
                    )
                ),
                spacing="3",
                width="100%",
                align="center",
            ),
            # Dialog sizing / positioning
            max_width="640px",
            width="100%",
            max_height="95vh",
            overflow="hidden",
        ),
        open=state.drawer_open,
        on_open_change=state.set_drawer_open,
    )
