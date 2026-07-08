"""Template detail drawer — read-only side panel for a selected TemplateDTO.

Rendered as a Radix Dialog (full-screen overlay constrained to a right panel)
so it stays consistent with the machine_drawer / request_drawer pattern used
elsewhere in the scaffold.

Groups displayed:
  - Identity   : template_id, name, description
  - Provider   : provider_api, provider_name, provider_type
  - Compute    : instance_type, image_id, max_instances, price_type, key_name
  - Network    : subnet_ids, security_group_ids, network_zones
  - Tags       : key/value table
  - Configuration / extra data : raw JSON view of provider_data / metadata
  - Metadata   : created_at, updated_at, is_active

Footer actions: Edit | Delete | Close
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import reflex as rx

from .drawer_utils import drawer_section as _drawer_section
from .request_modal import RequestModalState as _RequestModalState

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Private helpers  (mirrors style from machine_drawer.py)
# ---------------------------------------------------------------------------


def _field_row(label: str, value: Any) -> rx.Component:
    """A label/value pair displayed as a compact 2-column row.

    ``value`` may be a Reflex Var or a plain Python value. We never call
    ``str()`` on it — that would stringify the Var's compile-time
    expression, not the runtime value.
    """
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


def _field_row_code(label: str, value: Any) -> rx.Component:
    """A label/value pair where value is rendered in monospace."""
    return rx.hstack(
        rx.text(
            label,
            size="2",
            color=rx.color("gray", 11),
            min_width="9rem",
            flex_shrink="0",
        ),
        rx.code(value, size="1", word_break="break-all"),
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


def _list_pills(items: rx.Var) -> rx.Component:
    """Render a list[str] var as a row of code badges."""
    return rx.hstack(
        rx.foreach(
            items,
            lambda item: rx.code(item, size="1", variant="soft"),
        ),
        flex_wrap="wrap",
        spacing="1",
    )


# ---------------------------------------------------------------------------
# Delete confirmation sub-dialog
# ---------------------------------------------------------------------------


def delete_confirm_dialog(state: type) -> rx.Component:
    """Inline confirmation dialog for delete, nested inside the drawer dialog."""
    t = state.selected_template
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Delete template?"),
            rx.alert_dialog.description(
                rx.vstack(
                    rx.text(
                        "This permanently removes the template from ORB. "
                        "Existing machines created from it are not affected.",
                        size="2",
                    ),
                    rx.code(t["template_id"], size="1"),
                    spacing="2",
                    align="start",
                ),
            ),
            rx.hstack(
                rx.spacer(),
                rx.alert_dialog.cancel(
                    rx.button(
                        "Cancel",
                        variant="soft",
                        color_scheme="gray",
                        on_click=state.cancel_delete,
                    )
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Delete",
                        color_scheme="red",
                        on_click=state.confirm_delete,
                        loading=state.delete_loading,
                    )
                ),
                spacing="2",
                margin_top="1rem",
                width="100%",
            ),
        ),
        open=state.confirm_delete_open,
        on_open_change=state.cancel_delete,
    )


# ---------------------------------------------------------------------------
# Public component
# ---------------------------------------------------------------------------


def template_drawer(state: type) -> rx.Component:
    """Return a modal dialog that acts as a template detail drawer.

    Args:
        state: The TemplatesState class (passed as type to avoid circular import).

    Returns:
        A Radix dialog component controlled by state.drawer_open.
    """
    t = state.selected_template

    return rx.dialog.root(
        rx.dialog.content(
            # ── Header ──────────────────────────────────────────────────────
            rx.hstack(
                rx.vstack(
                    rx.heading(
                        rx.cond(
                            t["name"] != "",
                            t["name"],
                            t["template_id"],
                        ),
                        size="5",
                    ),
                    rx.hstack(
                        rx.code(t["template_id"], size="1"),
                        rx.badge(
                            t["provider_api"],
                            variant="soft",
                            color_scheme="blue",
                            size="1",
                        ),
                        spacing="2",
                        align="center",
                    ),
                    spacing="1",
                    align="start",
                ),
                rx.spacer(),
                rx.dialog.close(
                    rx.button(
                        rx.icon("x", size=16),
                        variant="ghost",
                        size="2",
                        on_click=state.close_drawer,
                        aria_label="Close drawer",
                    )
                ),
                width="100%",
                align="start",
                margin_bottom="1rem",
            ),
            rx.divider(margin_bottom="1.25rem"),
            # ── Scrollable content area ──────────────────────────────────────
            rx.box(
                # Identity
                _section(
                    "Identity",
                    _field_row_code("Template ID", t["template_id"]),
                    _field_row("Name", t["name"]),
                    _field_row("Description", t["description"]),
                ),
                # Provider
                _section(
                    "Provider",
                    _field_row("Provider API", t["provider_api"]),
                    _field_row("Provider Name", t["provider_name"]),
                    _field_row("Provider Type", t["provider_type"]),
                ),
                # Compute
                _section(
                    "Compute",
                    _field_row_code(
                        "Instance Type(s)", state.selected_template_instance_type_display
                    ),
                    _field_row_code("Image ID", t["image_id"]),
                    _field_row("Max Instances", t["max_instances"]),
                    _field_row("Price Type", t["price_type"]),
                    _field_row("Allocation Strategy", t["allocation_strategy"]),
                    _field_row("Key Name", t["key_name"]),
                ),
                # Network
                _section(
                    "Network",
                    rx.vstack(
                        rx.text("Subnet IDs", size="2", color=rx.color("gray", 11)),
                        rx.cond(
                            state.selected_template_has_subnets,
                            _list_pills(state.selected_template_subnet_ids),
                            rx.text("—", size="2"),
                        ),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.vstack(
                        rx.text("Security Group IDs", size="2", color=rx.color("gray", 11)),
                        rx.cond(
                            state.selected_template_has_sgs,
                            _list_pills(state.selected_template_sg_ids),
                            rx.text("—", size="2"),
                        ),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                ),
                # Tags
                rx.cond(
                    state.selected_template_has_tags,
                    _section(
                        "Tags",
                        rx.vstack(
                            rx.foreach(
                                state.selected_template_tags_list,
                                lambda kv: rx.hstack(
                                    rx.code(kv[0], size="1", variant="soft"),
                                    rx.text("=", size="2", color=rx.color("gray", 9)),
                                    rx.text(kv[1], size="2"),
                                    spacing="1",
                                    align="center",
                                ),
                            ),
                            spacing="1",
                            align="start",
                            width="100%",
                        ),
                    ),
                    rx.fragment(),
                ),
                # Extra configuration (provider_data as JSON)
                rx.cond(
                    state.selected_template_config_json != "",
                    _section(
                        "Configuration",
                        _json_box(state.selected_template_config_json),
                    ),
                    rx.fragment(),
                ),
                # Metadata
                _section(
                    "Metadata",
                    _field_row("Created at", t["created_at"]),
                    _field_row("Updated at", t["updated_at"]),
                    _field_row(
                        "Active",
                        rx.cond(t["is_active"], "Yes", "No"),
                    ),
                ),
                max_height="60vh",
                overflow_y="auto",
                padding_right="0.5rem",
            ),
            # ── Footer actions ───────────────────────────────────────────────
            rx.hstack(
                rx.button(
                    rx.icon("send", size=14),
                    "Request",
                    color_scheme="blue",
                    size="2",
                    on_click=_RequestModalState.open_for(t["template_id"]),
                ),
                rx.button(
                    rx.icon("pencil", size=14),
                    "Edit",
                    variant="outline",
                    size="2",
                    on_click=state.open_edit_from_drawer,
                ),
                rx.button(
                    rx.icon("trash-2", size=14),
                    "Delete",
                    variant="outline",
                    color_scheme="red",
                    size="2",
                    on_click=state.open_delete_confirm,
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
                margin_top="1.25rem",
                width="100%",
            ),
            max_width="560px",
            width="95vw",
        ),
        open=state.drawer_open,
    )
