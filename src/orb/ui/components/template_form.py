"""Template create/edit form rendered as a Radix Dialog.

Covers both create (form_mode == "create") and edit (form_mode == "edit") flows.
AWS-specific discovery endpoints (/aws/discovery, /aws/regions, /aws/sync-template,
/aws/cleanup-template, /config/defaults) are intentionally omitted — they are not
exposed by the ORB backend.  Network fields (subnet_ids, security_group_ids) are
accepted as plain text so the user can paste values manually until discovery is wired.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import reflex as rx

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Provider options — sourced from ORB React PoC; no live discovery needed.
# ---------------------------------------------------------------------------

PROVIDER_APIS: list[dict[str, str]] = [
    {"value": "aws", "label": "AWS"},
    {"value": "RunInstances", "label": "AWS - RunInstances"},
    {"value": "EC2Fleet", "label": "AWS - EC2Fleet"},
    {"value": "SpotFleet", "label": "AWS - SpotFleet"},
    {"value": "ASG", "label": "AWS - Auto Scaling Group"},
    {"value": "MicroVM", "label": "AWS - Lambda MicroVM"},
]

COMMON_INSTANCE_TYPES: list[str] = [
    "t3.nano",
    "t3.micro",
    "t3.small",
    "t3.medium",
    "t3.large",
    "t3.xlarge",
    "t4g.micro",
    "t4g.small",
    "t4g.medium",
    "m5.large",
    "m5.xlarge",
    "m6i.large",
    "m6i.xlarge",
    "c5.large",
    "c5.xlarge",
    "c6i.large",
    "r5.large",
]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _field_label(text: str, required: bool = False) -> rx.Component:
    return rx.hstack(
        rx.text(text, size="2", weight="medium", color=rx.color("gray", 12)),
        rx.cond(
            required,
            rx.text("*", size="2", color=rx.color("red", 9)),
            rx.fragment(),
        ),
        spacing="1",
        align="center",
        margin_bottom="0.25rem",
    )


def _hint(text: str) -> rx.Component:
    return rx.text(text, size="1", color=rx.color("gray", 10), margin_top="0.2rem")


def _section_heading(title: str) -> rx.Component:
    return rx.text(
        title,
        size="1",
        weight="bold",
        color=rx.color("gray", 11),
        text_transform="uppercase",
        letter_spacing="0.06em",
        margin_bottom="0.5rem",
        margin_top="0.25rem",
    )


def _input(
    value: rx.Var,
    on_change,
    placeholder: str = "",
    disabled: bool = False,
    font_family: str = "inherit",
) -> rx.Component:
    return rx.input(
        value=value,
        on_change=on_change,
        placeholder=placeholder,
        disabled=disabled,
        width="100%",
        font_family=font_family,
    )


def _textarea(
    value: rx.Var,
    on_change,
    placeholder: str = "",
    rows: int = 3,
    font_family: str = "inherit",
) -> rx.Component:
    return rx.text_area(
        value=value,
        on_change=on_change,
        placeholder=placeholder,
        rows=str(rows),
        width="100%",
        font_family=font_family,
    )


# ---------------------------------------------------------------------------
# Public component
# ---------------------------------------------------------------------------


def template_form(state: type) -> rx.Component:
    """Dialog with Create / Edit form for a template.

    Args:
        state: The TemplatesState class (passed as a type to avoid circular import).

    Returns:
        A Radix dialog.root controlled by ``state.form_open``.
    """
    fd = state.form_data
    is_edit = state.form_mode == "edit"
    is_loading = state.form_loading

    return rx.dialog.root(
        rx.dialog.content(
            # ── Header ──────────────────────────────────────────────────────
            rx.hstack(
                rx.vstack(
                    rx.heading(
                        rx.cond(is_edit, "Edit Template", "Add Template"),
                        size="5",
                    ),
                    rx.cond(
                        is_edit,
                        rx.code(fd["template_id"], size="1", color=rx.color("gray", 10)),
                        rx.fragment(),
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
                        on_click=state.close_form,
                        aria_label="Close form",
                    )
                ),
                width="100%",
                align="start",
                margin_bottom="1rem",
            ),
            rx.divider(margin_bottom="1rem"),
            # ── AWS discovery notice ─────────────────────────────────────────
            rx.callout.root(
                rx.callout.icon(rx.icon("info", size=16)),
                rx.callout.text(
                    "AWS resource discovery is not yet wired into the backend. "
                    "Enter VPC, subnet, and security-group IDs manually for now.",
                    size="2",
                ),
                color_scheme="blue",
                variant="surface",
                margin_bottom="1rem",
            ),
            # ── Scrollable form body ────────────────────────────────────────
            rx.box(
                rx.vstack(
                    # ── Identity ─────────────────────────────────────────────
                    _section_heading("Identity"),
                    rx.vstack(
                        _field_label("Template ID", required=True),
                        _input(
                            fd["template_id"],
                            lambda v: state.set_form_field("template_id", v),
                            placeholder="e.g. aws-t3-micro-abc123",
                            disabled=is_edit,
                            font_family="monospace",
                        ),
                        _hint("Unique identifier. Cannot be changed after creation."),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.vstack(
                        _field_label("Name"),
                        _input(
                            fd["name"],
                            lambda v: state.set_form_field("name", v),
                            placeholder="e.g. Spot fleet for batch jobs",
                        ),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.vstack(
                        _field_label("Description"),
                        _textarea(
                            fd["description"],
                            lambda v: state.set_form_field("description", v),
                            placeholder="Optional — describe the purpose of this template",
                            rows=2,
                        ),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.divider(),
                    # ── Compute ───────────────────────────────────────────────
                    _section_heading("Compute"),
                    rx.vstack(
                        _field_label("Provider API", required=True),
                        rx.select.root(
                            rx.select.trigger(width="100%"),
                            rx.select.content(
                                *[
                                    rx.select.item(p["label"], value=p["value"])
                                    for p in PROVIDER_APIS
                                ],
                            ),
                            value=fd["provider_api"],
                            on_change=lambda v: state.set_form_field("provider_api", v),
                            width="100%",
                        ),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.vstack(
                        _field_label("Instance Type"),
                        rx.hstack(
                            _input(
                                fd["instance_type"],
                                lambda v: state.set_form_field("instance_type", v),
                                placeholder="t3.micro",
                                font_family="monospace",
                            ),
                            spacing="2",
                            width="100%",
                        ),
                        rx.hstack(
                            *[
                                rx.button(
                                    t,
                                    size="1",
                                    variant="outline",
                                    on_click=lambda _t=t: state.set_form_field("instance_type", _t),
                                    font_family="monospace",
                                )
                                for t in ["t3.micro", "t3.medium", "m5.large", "c5.xlarge"]
                            ],
                            spacing="1",
                            flex_wrap="wrap",
                            margin_top="0.25rem",
                        ),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.vstack(
                        _field_label("Image ID (AMI)"),
                        _input(
                            fd["image_id"],
                            lambda v: state.set_form_field("image_id", v),
                            placeholder="ami-0abcdef1234567890",
                            font_family="monospace",
                        ),
                        _hint("Paste the AMI ID for your region directly."),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.vstack(
                        _field_label("Key Name"),
                        _input(
                            fd["key_name"],
                            lambda v: state.set_form_field("key_name", v),
                            placeholder="my-keypair",
                            font_family="monospace",
                        ),
                        _hint("Optional EC2 SSH key pair name."),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.divider(),
                    # ── Network ──────────────────────────────────────────────
                    _section_heading("Network"),
                    rx.vstack(
                        _field_label("Subnet IDs"),
                        _textarea(
                            fd["subnet_ids_text"],
                            lambda v: state.set_form_field("subnet_ids_text", v),
                            placeholder="subnet-0a1b2c3d\nsubnet-0e4f5a6b",
                            rows=2,
                            font_family="monospace",
                        ),
                        _hint("One subnet ID per line (or comma-separated)."),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.vstack(
                        _field_label("Security Group IDs"),
                        _textarea(
                            fd["security_group_ids_text"],
                            lambda v: state.set_form_field("security_group_ids_text", v),
                            placeholder="sg-0a1b2c3d4e5f6a7b8",
                            rows=2,
                            font_family="monospace",
                        ),
                        _hint("One security group ID per line (or comma-separated)."),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.divider(),
                    # ── User data ─────────────────────────────────────────────
                    _section_heading("User Data"),
                    rx.vstack(
                        _field_label("User data (cloud-init / bash)"),
                        _textarea(
                            fd["user_data"],
                            lambda v: state.set_form_field("user_data", v),
                            placeholder="#!/bin/bash\n# startup script",
                            rows=3,
                            font_family="monospace",
                        ),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.divider(),
                    # ── Tags ──────────────────────────────────────────────────
                    _section_heading("Tags"),
                    rx.vstack(
                        _field_label("Tags"),
                        _textarea(
                            fd["tags_text"],
                            lambda v: state.set_form_field("tags_text", v),
                            placeholder="Environment=dev\nManagedBy=orb",
                            rows=3,
                            font_family="monospace",
                        ),
                        _hint("key=value pairs, one per line or comma-separated."),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.divider(),
                    # ── Configuration (extra JSON) ────────────────────────────
                    _section_heading("Extra Configuration (JSON)"),
                    rx.vstack(
                        _field_label("Configuration"),
                        _textarea(
                            fd["configuration_json"],
                            lambda v: state.set_form_field("configuration_json", v),
                            placeholder='{\n  "max_instances": 10\n}',
                            rows=4,
                            font_family="monospace",
                        ),
                        _hint(
                            "Optional JSON map of additional provider-specific fields. "
                            "Must be valid JSON or left empty."
                        ),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    spacing="3",
                    align="start",
                    width="100%",
                    padding_bottom="1rem",
                ),
                max_height="60vh",
                overflow_y="auto",
                padding_right="0.25rem",
            ),
            # ── Validation errors ───────────────────────────────────────────
            rx.cond(
                state.form_errors.length() > 0,
                rx.callout.root(
                    rx.callout.icon(rx.icon("triangle-alert", size=16)),
                    rx.callout.text(
                        rx.vstack(
                            rx.foreach(
                                state.form_errors,
                                lambda e: rx.text(e, size="2"),
                            ),
                            spacing="1",
                            align="start",
                        ),
                    ),
                    color_scheme="red",
                    variant="surface",
                    margin_top="1rem",
                ),
                rx.fragment(),
            ),
            # ── Footer ──────────────────────────────────────────────────────
            rx.hstack(
                rx.spacer(),
                rx.button(
                    "Cancel",
                    variant="soft",
                    color_scheme="gray",
                    on_click=state.close_form,
                    disabled=is_loading,
                ),
                rx.button(
                    "Validate",
                    variant="outline",
                    on_click=state.validate_form,
                    loading=state.form_validating,
                    disabled=is_loading,
                ),
                rx.button(
                    rx.cond(is_edit, "Save Changes", "Create Template"),
                    on_click=state.submit_form,
                    loading=is_loading,
                ),
                spacing="2",
                margin_top="1.25rem",
                width="100%",
            ),
            max_width="640px",
            width="95vw",
        ),
        open=state.form_open,
    )
