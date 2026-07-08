"""Templates list page.

Replicates the React PoC Templates page MINUS the phantom AWS-specific
endpoints (/aws/discovery, /aws/regions, /aws/sync-template,
/aws/cleanup-template, /config/defaults).

Layout:
  - Top toolbar: count badge, view toggle, column picker, filter pills,
    Refresh + Create buttons
  - Switchable list/grid view (list_grid_view component)
  - Click card  → TemplateDrawer (read-only detail panel)
  - Edit button → TemplateForm pre-filled
  - Delete      → inline confirm dialog inside TemplateDrawer
  - Empty state with Create CTA
  - Error callout on API failure
  - Loading skeleton while fetching
"""

from __future__ import annotations

import asyncio
import json
import random
import string
from typing import Any

import reflex as rx

from .. import api
from ..components.cell_formatters import bool_badge, json_truncate, list_count
from ..components.column_picker import column_picker
from ..components.error_callout import error_callout
from ..components.layout import page
from ..components.list_grid_view import ColumnDef, list_grid_view
from ..components.list_page_shell import list_page_shell
from ..components.provider_columns import build_provider_columns, resolve_provider_row_fields
from ..components.refresh_control import refresh_control
from ..components.request_modal import RequestModalState, request_modal, request_success_banner
from ..components.template_drawer import delete_confirm_dialog, template_drawer
from ..components.template_form import template_form
from ..components.view_toggle import view_toggle
from ..state import AppState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Filter options matching the React PoC TYPE_FILTERS list
PROVIDER_FILTER_OPTIONS: list[str] = ["All", "aws", "RunInstances", "EC2Fleet", "SpotFleet", "ASG"]

# Color mapping for provider_api badges
PROVIDER_COLOR_MAP: dict[str, str] = {
    "SpotFleet": "purple",
    "EC2Fleet": "blue",
    "RunInstances": "teal",
    "ASG": "orange",
    "aws": "indigo",
}

# Empty template dict used as the default for form_data / selected_template
# Every key that the Drawer or Form references must be present here so that
# rx.State can compute the initial Var types correctly.
_EMPTY_TEMPLATE: dict[str, Any] = {
    "template_id": "",
    "name": "",
    "description": "",
    "provider_api": "aws",
    "provider_name": "",
    "provider_type": "",
    "instance_type": "",
    "image_id": "",
    "max_instances": 1,
    "price_type": "ondemand",
    "allocation_strategy": "",
    "key_name": "",
    "subnet_ids": [],
    "security_group_ids": [],
    "network_zones": [],
    "user_data": "",
    "tags": {},
    "provider_data": {},
    "metadata": {},
    "created_at": "",
    "updated_at": "",
    "is_active": True,
    # Fleet / mixed-instance type dicts — absent on single-VM templates.
    "machine_types": {},
    "machine_types_ondemand": {},
    "machine_types_priority": {},
}

# Empty form data dict — separate from template dict; includes text-encoded
# list fields and JSON fields for manual user entry.
_EMPTY_FORM: dict[str, Any] = {
    "template_id": "",
    "name": "",
    "description": "",
    "provider_api": "aws",
    "instance_type": "t3.micro",
    "image_id": "",
    "key_name": "",
    "subnet_ids_text": "",
    "security_group_ids_text": "",
    "user_data": "",
    "tags_text": "Environment=dev, ManagedBy=orb",
    "configuration_json": "",
    "version": "1.0",
}

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

MAX_VISIBLE_COLUMNS = 5


def _active_badge(row) -> rx.Component:
    return rx.cond(
        row["is_active"],
        rx.badge("Active", variant="soft", color_scheme="green", size="1"),
        rx.badge("Inactive", variant="soft", color_scheme="gray", size="1"),
    )


def _id_code(row) -> rx.Component:
    return rx.code(row["template_id"], size="1")


def _provider_badge_cell(row) -> rx.Component:
    return rx.badge(
        row["provider_api"],
        variant="soft",
        color_scheme=row["badge_color"],
        size="1",
    )


def _truncate_id(row) -> rx.Component:
    return rx.code(
        row["image_id"],
        size="1",
        white_space="nowrap",
        overflow="hidden",
        text_overflow="ellipsis",
        max_width="8rem",
        display="inline-block",
    )


def _template_row_actions(row) -> rx.Component:
    """Icon-only per-row actions for the templates list view.

    Mirrors machines/requests row actions: variant=ghost (no background
    tint), so the actions blend into the row instead of looking like a
    pill cluster. Request keeps color_scheme=blue + icon-only.
    """
    return rx.hstack(
        rx.icon_button(
            rx.icon("send", size=14),
            size="1",
            variant="ghost",
            color_scheme="blue",
            on_click=RequestModalState.open_for(row["template_id"]),
            title="Request machines from this template",
        ),
        rx.icon_button(
            rx.icon("eye", size=14),
            size="1",
            variant="ghost",
            on_click=TemplatesState.open_drawer(row["raw"]),
            title="View template details",
        ),
        rx.icon_button(
            rx.icon("pencil", size=14),
            size="1",
            variant="ghost",
            on_click=TemplatesState.open_edit(row["raw"]),
            title="Edit template",
        ),
        spacing="2",
        align="center",
        justify="end",
    )


TEMPLATE_COLUMNS: list[ColumnDef] = [
    # --- Locked columns ---
    ColumnDef("is_active", "Active", default_visible=True, lockable=True, formatter=_active_badge),
    ColumnDef("template_id", "ID", default_visible=True, lockable=True, formatter=_id_code),
    # --- Default visible (5) ---
    ColumnDef("provider_api", "Provider API", default_visible=True, formatter=_provider_badge_cell),
    # Single "Instance Type(s)" column — renders one type for single-VM
    # templates, "t3.medium×2, t3.xlarge×4" for fleet/mixed templates.
    # The card_rows mapper overwrites the raw ``instance_type`` field with
    # the multi-aware display string, so there's only one column to manage.
    ColumnDef("instance_type", "Instance Type(s)", default_visible=True),
    ColumnDef("max_instances", "Max", default_visible=True, align="end"),
    ColumnDef("price_type", "Pricing", default_visible=True),
    # --- All remaining DTO fields (default_visible=False) ---
    ColumnDef("name", "Name", default_visible=False),
    ColumnDef("description", "Description", default_visible=False),
    ColumnDef("image_id", "Image", default_visible=False, formatter=_truncate_id),
    ColumnDef("allocation_strategy", "Allocation", default_visible=False),
    ColumnDef("max_price", "Max Price", default_visible=False, align="end"),
    ColumnDef("root_device_volume_size", "Vol Size", default_visible=False, align="end"),
    ColumnDef("volume_type", "Vol Type", default_visible=False),
    ColumnDef("iops", "IOPS", default_visible=False, align="end"),
    ColumnDef("throughput", "Throughput", default_visible=False, align="end"),
    ColumnDef(
        "storage_encryption",
        "Encrypted",
        default_visible=False,
        formatter=bool_badge("storage_encryption"),
    ),
    ColumnDef("encryption_key", "Enc Key", default_visible=False),
    ColumnDef("key_name", "Key Name", default_visible=False),
    ColumnDef("instance_profile", "Profile", default_visible=False),
    ColumnDef("launch_template_id", "Launch Tmpl", default_visible=False),
    ColumnDef(
        "monitoring_enabled",
        "Monitoring",
        default_visible=False,
        formatter=bool_badge("monitoring_enabled"),
    ),
    ColumnDef(
        "public_ip_assignment",
        "Public IP",
        default_visible=False,
        formatter=bool_badge("public_ip_assignment"),
    ),
    ColumnDef("subnet_ids", "Subnets", default_visible=False, formatter=list_count("subnet_ids")),
    ColumnDef(
        "security_group_ids",
        "Sec Groups",
        default_visible=False,
        formatter=list_count("security_group_ids"),
    ),
    ColumnDef(
        "network_zones", "Net Zones", default_visible=False, formatter=list_count("network_zones")
    ),
    ColumnDef(
        "machine_types",
        "Mach Types",
        default_visible=False,
        formatter=json_truncate("machine_types"),
    ),
    ColumnDef(
        "machine_types_ondemand",
        "Ondemand Types",
        default_visible=False,
        formatter=json_truncate("machine_types_ondemand"),
    ),
    ColumnDef(
        "machine_types_priority",
        "Priority Types",
        default_visible=False,
        formatter=json_truncate("machine_types_priority"),
    ),
    ColumnDef("tags", "Tags", default_visible=False, formatter=json_truncate("tags")),
    ColumnDef("metadata", "Metadata", default_visible=False, formatter=json_truncate("metadata")),
    ColumnDef(
        "provider_data",
        "Provider Data",
        default_visible=False,
        formatter=json_truncate("provider_data"),
    ),
    ColumnDef("provider_type", "Provider Type", default_visible=False),
    ColumnDef("provider_name", "Provider Name", default_visible=False),
    ColumnDef("user_data", "User Data", default_visible=False),
    ColumnDef("version", "Version", default_visible=False),
    ColumnDef("updated_at", "Updated", default_visible=False, sortable=True),
    ColumnDef("created_at", "Created", default_visible=False, sortable=True),
    # --- Locked actions column (rightmost) ---
    ColumnDef(
        "_actions",
        "Actions",
        default_visible=True,
        lockable=True,
        formatter=_template_row_actions,
        align="end",
    ),
]

# Default visible column keys (locked + 5 non-locked defaults).
# Stored as a FENCED comma-separated string ",k1,k2,...,kN," so that
# .contains(",key,") does exact-key matching (avoids "name" ⊂ "key_name").
_DEFAULT_VISIBLE_COLS = (
    ",is_active,template_id,provider_api,instance_type,max_instances,price_type,_actions,"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rand_suffix(n: int = 6) -> str:
    """Return a short alphanumeric random string for template ID generation."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _parse_tags(text: str) -> dict[str, str]:
    """Parse 'key=value' text (comma or newline separated) into a dict."""
    out: dict[str, str] = {}
    for part in text.replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


def _parse_ids_text(text: str) -> list[str]:
    """Parse newline/comma-separated IDs into a list, stripping blanks."""
    ids = []
    for part in text.replace("\n", ",").split(","):
        part = part.strip()
        if part:
            ids.append(part)
    return ids


def _safe_str(v: Any) -> str:
    return str(v) if v is not None else ""


def _template_to_display(raw: Any) -> dict[str, Any]:
    """Normalise a raw API dict to the _EMPTY_TEMPLATE shape."""
    if not isinstance(raw, dict):
        return dict(_EMPTY_TEMPLATE)
    return {
        "template_id": _safe_str(raw.get("template_id", "")),
        "name": _safe_str(raw.get("name", "")),
        "description": _safe_str(raw.get("description", "")),
        "provider_api": _safe_str(raw.get("provider_api", "")),
        "provider_name": _safe_str(raw.get("provider_name", "")),
        "provider_type": _safe_str(raw.get("provider_type", "")),
        "instance_type": _safe_str(raw.get("instance_type", "")),
        "image_id": _safe_str(raw.get("image_id", "")),
        "max_instances": raw.get("max_instances") or raw.get("max_capacity") or 1,
        "price_type": _safe_str(raw.get("price_type", "ondemand")),
        "allocation_strategy": _safe_str(raw.get("allocation_strategy", "")),
        "key_name": _safe_str(raw.get("key_name", "")),
        "subnet_ids": raw.get("subnet_ids") or [],
        "security_group_ids": raw.get("security_group_ids") or [],
        "network_zones": raw.get("network_zones") or [],
        "user_data": _safe_str(raw.get("user_data", "")),
        "tags": raw.get("tags") or {},
        "provider_data": raw.get("provider_data") or {},
        "metadata": raw.get("metadata") or {},
        "created_at": _safe_str(raw.get("created_at", "")),
        "updated_at": _safe_str(raw.get("updated_at", "")),
        "is_active": bool(raw.get("is_active", True)),
        # Fleet / mixed-instance type dicts — absent on single-VM templates.
        # Preserve raw dicts so _instance_types_display can render them.
        "machine_types": raw.get("machine_types") or {},
        "machine_types_ondemand": raw.get("machine_types_ondemand") or {},
        "machine_types_priority": raw.get("machine_types_priority") or {},
    }


def _template_to_form(t: dict[str, Any]) -> dict[str, Any]:
    """Populate form_data from a template dict (for edit flow)."""
    tags = t.get("tags") or {}
    tags_text = ", ".join(f"{k}={v}" for k, v in tags.items() if k != "CreatedBy")
    subnet_text = "\n".join(t.get("subnet_ids") or [])
    sg_text = "\n".join(t.get("security_group_ids") or [])

    # configuration_json: any extra provider data we want to expose
    pd = t.get("provider_data") or {}
    config_json = json.dumps(pd, indent=2) if pd else ""

    return {
        "template_id": _safe_str(t.get("template_id", "")),
        "name": _safe_str(t.get("name", "")),
        "description": _safe_str(t.get("description", "")),
        "provider_api": _safe_str(t.get("provider_api", "aws")),
        "instance_type": _safe_str(t.get("instance_type", "")),
        "image_id": _safe_str(t.get("image_id", "")),
        "key_name": _safe_str(t.get("key_name", "")),
        "subnet_ids_text": subnet_text,
        "security_group_ids_text": sg_text,
        "user_data": _safe_str(t.get("user_data", "")),
        "tags_text": tags_text,
        "configuration_json": config_json,
        "version": _safe_str(t.get("version", "1.0")),
    }


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class TemplatesState(AppState):
    """State for the Templates page."""

    # ── Data ────────────────────────────────────────────────────────────────
    templates: list[dict[str, Any]] = []
    loading: bool = False
    error: str = ""

    # ── Drawer ──────────────────────────────────────────────────────────────
    drawer_open: bool = False
    selected_template: dict[str, Any] = dict(_EMPTY_TEMPLATE)

    # ── Delete confirm ───────────────────────────────────────────────────────
    confirm_delete_open: bool = False
    delete_loading: bool = False

    # ── Form ────────────────────────────────────────────────────────────────
    form_open: bool = False
    form_mode: str = "create"  # "create" | "edit"
    form_data: dict[str, str] = dict(_EMPTY_FORM)
    form_errors: list[str] = []
    form_loading: bool = False
    form_validating: bool = False

    # ── Filter ──────────────────────────────────────────────────────────────
    active_filter: str = "All"

    # Search text for client-side filtering
    search_text: str = ""

    # Provider filter (persisted in localStorage)
    provider_filter: str = rx.LocalStorage("All", name="orb-templates-provider-filter")

    # ── Auto-refresh (persisted in localStorage) ─────────────────────────────
    auto_refresh_enabled: str = rx.LocalStorage("false", name="orb-templates-auto-refresh-enabled")
    auto_refresh_interval: str = rx.LocalStorage("10", name="orb-templates-auto-refresh-interval")

    # Last refresh timestamp (for display)
    last_refresh: str = ""

    # ── View preferences (persisted in localStorage) ─────────────────────────
    view_mode: str = rx.LocalStorage("list", name="orb-templates-view-mode")
    visible_columns: str = rx.LocalStorage(
        _DEFAULT_VISIBLE_COLS,
        name="orb-templates-visible-columns",
    )
    sort_key: str = rx.LocalStorage("", name="orb-templates-sort-key")
    sort_dir: str = rx.LocalStorage("asc", name="orb-templates-sort-dir")

    # ── Pagination ───────────────────────────────────────────────────────────
    next_cursor: str = ""
    api_total_count: int = 0
    loading_more: bool = False
    page_size: int = 200

    # ── Computed ────────────────────────────────────────────────────────────

    @rx.var
    def total_count(self) -> int:
        """Backend total-match count (or local count when not yet set by the API)."""
        return self.api_total_count if self.api_total_count > 0 else len(self.templates)

    @rx.var
    def loaded_count(self) -> int:
        """Number of rows currently held in memory (across all fetched pages)."""
        return len(self.templates)

    @rx.var
    def filtered_templates(self) -> list[dict[str, Any]]:
        result = self.templates
        # Apply provider_api filter pill
        if self.active_filter != "All":
            result = [t for t in result if t.get("provider_api") == self.active_filter]
        # Apply search text filter
        q = self.search_text.lower().strip()
        if q:
            result = [
                t
                for t in result
                if q in (t.get("template_id") or "").lower()
                or q in (t.get("name") or "").lower()
                or q in (t.get("description") or "").lower()
            ]
        return result

    @rx.var
    def filtered_count(self) -> int:
        return len(self.filtered_templates)

    @rx.var
    def dynamic_columns(self) -> list[ColumnDef]:
        """Provider-declared column definitions merged from backend schemas.

        Reads ``self.provider_schemas`` — inherited from ``AppState`` via
        Reflex's substate mechanism, so a single HTTP fetch on page mount
        populates every list-page's dynamic columns from the same source.
        """
        return build_provider_columns(
            self.provider_schemas,
            "templates",
            self.provider_filter,
        )

    @rx.var
    def card_rows(self) -> list[dict[str, Any]]:
        """Pre-formatted row data — Vars-safe (no Python ops in template).
        Resolves CreatedBy tag, badge color, and all column fields ahead of
        time.  Used by both list view cells and grid card renderer.
        All dict/list fields are pre-serialised to strings here so that
        Reflex column formatters receive typed scalars at compile time.
        """
        # Provider schemas inherited from AppState via substate.
        _tmpl_schemas = self.provider_schemas

        rows: list[dict[str, Any]] = []
        for t in self.filtered_templates:
            tags = t.get("tags") or {}
            if not isinstance(tags, dict):
                tags = {}
            created_by = str(tags.get("CreatedBy") or "") if tags else ""
            provider_api = str(t.get("provider_api") or "")
            badge_color = PROVIDER_COLOR_MAP.get(provider_api, "gray")

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

            # Build instance_types_display: prefer ondemand+priority dicts,
            # fall back to machine_types, then to single instance_type field.
            def _instance_types_display(raw: Any) -> str:
                od = raw.get("machine_types_ondemand") or {}
                pr = raw.get("machine_types_priority") or {}
                combined: dict[str, Any] = {}
                if isinstance(od, dict):
                    combined.update(od)
                if isinstance(pr, dict):
                    combined.update(pr)
                if not combined:
                    mt = raw.get("machine_types") or {}
                    if isinstance(mt, dict):
                        combined.update(mt)
                if combined:
                    parts = [f"{itype}x{weight}" for itype, weight in combined.items()]
                    return ", ".join(parts)
                # Final fallback: single instance_type field
                single = raw.get("instance_type") or ""
                return str(single) if single else ""

            rows.append(
                {
                    # ── Core identity ──────────────────────────────────────────
                    "is_active": bool(t.get("is_active", True)),
                    "template_id": t.get("template_id") or "",
                    "name": t.get("name") or "",
                    "description": t.get("description") or "",
                    # ── Provider ───────────────────────────────────────────────
                    "provider_api": provider_api or "—",
                    "provider_name": t.get("provider_name") or "",
                    "provider_type": t.get("provider_type") or "",
                    # ── Instance config ────────────────────────────────────────
                    # Single column: multi-VM aware. For fleet templates this
                    # produces "t3.medium×2, t3.xlarge×4"; for single-VM
                    # templates it falls back to the bare instance_type field.
                    "instance_type": _instance_types_display(t) or (t.get("instance_type") or ""),
                    "image_id": t.get("image_id") or "",
                    "max_instances": t.get("max_instances") or 0,
                    # ── Pricing ────────────────────────────────────────────────
                    "price_type": t.get("price_type") or "",
                    "allocation_strategy": t.get("allocation_strategy") or "",
                    "max_price": t.get("max_price") if t.get("max_price") is not None else 0.0,
                    # ── Storage ────────────────────────────────────────────────
                    "root_device_volume_size": t.get("root_device_volume_size")
                    if t.get("root_device_volume_size") is not None
                    else 0,
                    "volume_type": t.get("volume_type") or "",
                    "iops": t.get("iops") if t.get("iops") is not None else 0,
                    "throughput": t.get("throughput") if t.get("throughput") is not None else 0,
                    "storage_encryption": bool(t.get("storage_encryption", False)),
                    "encryption_key": t.get("encryption_key") or "",
                    # ── Access ─────────────────────────────────────────────────
                    "key_name": t.get("key_name") or "",
                    "user_data": t.get("user_data") or "",
                    "instance_profile": t.get("instance_profile") or "",
                    "launch_template_id": t.get("launch_template_id") or "",
                    # ── Advanced ───────────────────────────────────────────────
                    "monitoring_enabled": bool(t.get("monitoring_enabled", False)),
                    "public_ip_assignment": bool(t.get("public_ip_assignment", False)),
                    # ── Network lists (pre-formatted as count strings) ─────────
                    "subnet_ids": _list_str(t.get("subnet_ids")),
                    "security_group_ids": _list_str(t.get("security_group_ids")),
                    "network_zones": _list_str(t.get("network_zones")),
                    # ── Machine type dicts (truncated JSON) ────────────────────
                    "machine_types": _json_str(t.get("machine_types")),
                    "machine_types_ondemand": _json_str(t.get("machine_types_ondemand")),
                    "machine_types_priority": _json_str(t.get("machine_types_priority")),
                    # ── Tags / metadata / provider_data (truncated JSON) ───────
                    "tags": _json_str(tags),
                    "metadata": _json_str(t.get("metadata")),
                    "provider_data": _json_str(t.get("provider_data")),
                    # ── Timestamps ─────────────────────────────────────────────
                    "updated_at": t.get("updated_at") or "",
                    "created_at": t.get("created_at") or "",
                    # ── Versioning ─────────────────────────────────────────────
                    "version": str(t.get("version") or ""),
                    # ── Keys used by grid card renderer ───────────────────────
                    "has_created_by": bool(created_by),
                    "created_by": created_by,
                    "badge_color": badge_color,
                    "raw": t,
                    # ── Provider-declared fields ───────────────────────────────
                    **resolve_provider_row_fields(
                        t,
                        _tmpl_schemas,
                        "templates",
                        self.provider_filter,
                    ),
                }
            )
        return rows

    @rx.var
    def sorted_rows(self) -> list[dict[str, Any]]:
        """card_rows sorted by sort_key / sort_dir.

        When sort_key is empty returns rows in their natural (filtered) order.
        Sorting is done on pre-formatted string values so it is consistent with
        what the user sees in the list view cells.
        """
        rows = self.card_rows
        key = self.sort_key
        if not key:
            return rows
        reverse = self.sort_dir == "desc"
        try:
            return sorted(rows, key=lambda r: str(r.get(key, "") or ""), reverse=reverse)
        except Exception:
            return rows

    # ── Per-provider counts for filter pills ────────────────────────────────

    @rx.var
    def provider_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in self.templates:
            api_val = str(t.get("provider_api") or "")
            counts[api_val] = counts.get(api_val, 0) + 1
        return counts

    # ── Helpers for drawer rendering (computed vars) ─────────────────────────

    @rx.var
    def selected_template_instance_type_display(self) -> str:
        """Multi-VM-aware instance type display for the detail drawer.

        Mirrors the _instance_types_display logic from card_rows so the
        drawer always shows the same string as the list/grid view.
        For fleet/mixed templates this produces "t3.medium x 2, t3.xlarge x 4";
        for single-VM templates it falls back to the bare instance_type field.
        """
        t = self.selected_template
        od = t.get("machine_types_ondemand") or {}
        pr = t.get("machine_types_priority") or {}
        combined: dict[str, Any] = {}
        if isinstance(od, dict):
            combined.update(od)
        if isinstance(pr, dict):
            combined.update(pr)
        if not combined:
            mt = t.get("machine_types") or {}
            if isinstance(mt, dict):
                combined.update(mt)
        if combined:
            parts = [f"{itype}x{weight}" for itype, weight in combined.items()]
            return ", ".join(parts)
        return str(t.get("instance_type") or "")

    @rx.var
    def selected_template_has_tags(self) -> bool:
        tags = self.selected_template.get("tags")
        return bool(tags and isinstance(tags, dict) and len(tags) > 0)

    @rx.var
    def selected_template_tags_list(self) -> list[list[str]]:
        tags = self.selected_template.get("tags") or {}
        if not isinstance(tags, dict):
            return []
        return [[str(k), str(v)] for k, v in tags.items()]

    @rx.var
    def selected_template_subnet_ids(self) -> list[str]:
        sn = self.selected_template.get("subnet_ids") or []
        return [str(x) for x in sn] if isinstance(sn, list) else []

    @rx.var
    def selected_template_sg_ids(self) -> list[str]:
        sg = self.selected_template.get("security_group_ids") or []
        return [str(x) for x in sg] if isinstance(sg, list) else []

    @rx.var
    def selected_template_has_subnets(self) -> bool:
        sn = self.selected_template.get("subnet_ids")
        return bool(sn and isinstance(sn, list) and len(sn) > 0)

    @rx.var
    def selected_template_has_sgs(self) -> bool:
        sg = self.selected_template.get("security_group_ids")
        return bool(sg and isinstance(sg, list) and len(sg) > 0)

    @rx.var
    def selected_template_config_json(self) -> str:
        pd = self.selected_template.get("provider_data")
        if not pd or not isinstance(pd, dict) or not pd:
            return ""
        try:
            return json.dumps(pd, indent=2)
        except Exception:
            return str(pd)

    # ── Events ──────────────────────────────────────────────────────────────

    def _normalize_visible_columns(self) -> None:
        """Ensure visible_columns uses the fenced format ,k1,k2,...,kN,.

        Old localStorage values (from sessions before the fenced format was
        introduced) arrive without the leading/trailing comma.  This method
        migrates them in-place on first access.  Safe to call multiple times.
        """
        vc = self.visible_columns
        if vc and not vc.startswith(","):
            keys = [k for k in vc.split(",") if k]
            self.visible_columns = "," + ",".join(keys) + "," if keys else ","

    @rx.event
    async def load(self):
        import datetime as _dt

        self._normalize_visible_columns()
        self.loading = True
        self.error = ""
        # Reset pagination for a fresh load
        self.next_cursor = ""
        self.api_total_count = 0
        try:
            res = await api.list_templates(limit=self.page_size)
            raw_list = res.get("templates", [])
            if not isinstance(raw_list, list):
                raw_list = []
            self.templates = [_template_to_display(t) for t in raw_list]
            self.next_cursor = res.get("next_cursor") or ""
            self.api_total_count = int(res.get("total_count") or len(raw_list))
            self.last_refresh = _dt.datetime.now().strftime("%H:%M:%S")
        except Exception as e:
            self.error = f"Failed to load templates: {e}"
        finally:
            self.loading = False

    @rx.event
    async def refresh(self):
        """Reload template list from the backend cache."""
        yield TemplatesState.load

    @rx.event
    async def force_refresh(self):
        """Force-refresh provider-managed templates (re-runs discovery)."""
        self.loading = True
        self.error = ""
        try:
            await api.refresh_templates()
        except Exception as e:
            self.error = f"Failed to refresh provider templates: {e}"
        finally:
            self.loading = False
        yield TemplatesState.load

    # ── View preference events ────────────────────────────────────────────────

    @rx.event
    def set_view_mode(self, mode: str):
        self.view_mode = mode

    @rx.event
    def toggle_column(self, key: str, checked: bool):
        # visible_columns is stored as ",k1,k2,...,kN," (fenced format).
        # Split and strip empty strings; the fence commas yield "" entries.
        keys = [k for k in self.visible_columns.split(",") if k]
        locked_keys = {c.key for c in TEMPLATE_COLUMNS if c.lockable}
        visible_non_locked = [k for k in keys if k not in locked_keys]
        if checked and key not in keys:
            if len(visible_non_locked) >= MAX_VISIBLE_COLUMNS:
                # Soft cap: silently drop the oldest non-locked column to make room
                keys.remove(visible_non_locked[0])
            keys.append(key)
        elif not checked and key in keys:
            keys.remove(key)
        # Re-encode with fence commas so ,key, matching stays exact
        self.visible_columns = "," + ",".join(keys) + "," if keys else ","

    @rx.event
    def set_sort(self, key: str):
        if self.sort_key == key:
            self.sort_dir = "desc" if self.sort_dir == "asc" else "asc"
        else:
            self.sort_key = key
            self.sort_dir = "asc"

    # ── Drawer events ────────────────────────────────────────────────────────

    @rx.event
    def open_drawer(self, template: dict[str, Any]):
        self.selected_template = _template_to_display(template)
        self.drawer_open = True
        self.confirm_delete_open = False

    @rx.event
    def close_drawer(self):
        self.drawer_open = False
        self.confirm_delete_open = False

    @rx.event
    def open_delete_confirm(self):
        self.confirm_delete_open = True

    @rx.event
    def open_delete_for(self, template: dict[str, Any]):
        """Open the delete confirm directly from a card row."""
        self.selected_template = _template_to_display(template)
        self.confirm_delete_open = True

    @rx.event
    def cancel_delete(self):
        self.confirm_delete_open = False

    @rx.event
    async def confirm_delete(self):
        self.delete_loading = True
        tid = self.selected_template.get("template_id", "")
        try:
            await api.delete_template(tid)
            self.drawer_open = False
            self.confirm_delete_open = False
            yield TemplatesState.load
        except Exception as e:
            self.error = f"Delete failed: {e}"
        finally:
            self.delete_loading = False

    @rx.event
    def open_edit_from_drawer(self):
        """Open the edit form pre-filled from the currently open drawer."""
        self.form_data = _template_to_form(self.selected_template)
        self.form_mode = "edit"
        self.form_errors = []
        self.form_open = True
        self.drawer_open = False

    # ── Form events ──────────────────────────────────────────────────────────

    @rx.event
    def open_create(self):
        provider = "aws"
        instance = "t3.micro"
        suffix = _rand_suffix()
        generated_id = f"{provider}-{instance.replace('.', '-')}-{suffix}"
        self.form_data = {
            **_EMPTY_FORM,
            "template_id": generated_id,
        }
        self.form_mode = "create"
        self.form_errors = []
        self.form_open = True

    @rx.event
    def open_edit(self, template: dict[str, Any]):
        self.form_data = _template_to_form(_template_to_display(template))
        self.form_mode = "edit"
        self.form_errors = []
        self.form_open = True

    @rx.event
    def close_form(self):
        self.form_open = False
        self.form_errors = []

    @rx.event
    def set_form_field(self, name: str, value: str):
        self.form_data = {**self.form_data, name: value}
        # Re-generate template_id for create mode when provider or instance changes
        if self.form_mode == "create" and name in ("provider_api", "instance_type"):
            provider = self.form_data.get("provider_api", "aws")
            instance = self.form_data.get("instance_type", "")
            suffix = _rand_suffix()

            def safe(s: str) -> str:
                return s.lower().replace(".", "-").replace("_", "-")

            parts = [p for p in [safe(provider), safe(instance), suffix] if p]
            self.form_data = {**self.form_data, "template_id": "-".join(parts)}

    def _validate_form_data(self) -> list[str]:
        """Return a list of validation error strings, empty if valid."""
        errors = []
        fd = self.form_data

        if not str(fd.get("template_id", "")).strip():
            errors.append("Template ID is required.")

        cfg_raw = str(fd.get("configuration_json", "")).strip()
        if cfg_raw:
            try:
                json.loads(cfg_raw)
            except json.JSONDecodeError as e:
                errors.append(f"Configuration JSON is invalid: {e}")

        return errors

    def _build_body(self) -> dict[str, Any]:
        """Build the API request body from form_data."""
        fd = self.form_data
        tags = _parse_tags(str(fd.get("tags_text", "")))
        tags["CreatedBy"] = "orb-ui"

        subnet_ids = _parse_ids_text(str(fd.get("subnet_ids_text", "")))
        sg_ids = _parse_ids_text(str(fd.get("security_group_ids_text", "")))

        cfg_raw = str(fd.get("configuration_json", "")).strip()
        extra_config: dict[str, Any] = {}
        if cfg_raw:
            try:
                extra_config = json.loads(cfg_raw)
            except json.JSONDecodeError:
                extra_config = {}

        body: dict[str, Any] = {
            "template_id": str(fd.get("template_id", "")).strip(),
            "name": str(fd.get("name", "")).strip() or str(fd.get("template_id", "")).strip(),
            "provider_api": str(fd.get("provider_api", "aws")),
            "image_id": str(fd.get("image_id", "")).strip(),
            "instance_type": str(fd.get("instance_type", "")).strip(),
            "tags": tags,
            "version": str(fd.get("version", "1.0")),
        }

        desc = str(fd.get("description", "")).strip()
        if desc:
            body["description"] = desc

        key = str(fd.get("key_name", "")).strip()
        if key:
            body["key_name"] = key

        ud = str(fd.get("user_data", "")).strip()
        if ud:
            body["user_data"] = ud

        if subnet_ids:
            body["subnet_ids"] = subnet_ids

        if sg_ids:
            body["security_group_ids"] = sg_ids

        # Merge any extra configuration fields into the body so the backend
        # can route them via the `configuration` pass-through field.
        if extra_config and isinstance(extra_config, dict):
            body["configuration"] = extra_config

        return body

    @rx.event
    async def validate_form(self):
        """Call the backend validate endpoint and surface errors."""
        client_errors = self._validate_form_data()
        if client_errors:
            self.form_errors = client_errors
            return

        self.form_validating = True
        self.form_errors = []
        try:
            body = self._build_body()
            result = await api.validate_template(body)
            errors = result.get("errors") or []
            if not result.get("valid", True) or errors:
                self.form_errors = (
                    [str(e) for e in errors] if errors else ["Template validation failed."]
                )
            else:
                # Show transient success — no toast system yet; clear errors
                self.form_errors = []
        except Exception as e:
            self.form_errors = [f"Validation request failed: {e}"]
        finally:
            self.form_validating = False

    @rx.event
    async def submit_form(self):
        """Create or update template, then reload the list."""
        client_errors = self._validate_form_data()
        if client_errors:
            self.form_errors = client_errors
            return

        self.form_loading = True
        self.form_errors = []
        try:
            body = self._build_body()
            if self.form_mode == "create":
                result = await api.create_template(body)
                if not result.get("created", True):
                    ve = result.get("validation_errors") or []
                    self.form_errors = [str(e) for e in ve] if ve else ["Template creation failed."]
                    return
            else:
                template_id = body.pop("template_id", self.form_data.get("template_id", ""))
                result = await api.update_template(template_id, body)
                if not result.get("updated", True):
                    ve = result.get("validation_errors") or []
                    self.form_errors = [str(e) for e in ve] if ve else ["Template update failed."]
                    return

            self.form_open = False
            yield TemplatesState.load
        except Exception as e:
            self.form_errors = [f"Request failed: {e}"]
        finally:
            self.form_loading = False

    # ── Pagination ───────────────────────────────────────────────────────────

    @rx.event(background=True)
    async def load_more(self) -> None:
        """Append the next page of templates to the in-memory list.

        State-locking pattern: read ``loading_more`` and ``next_cursor``
        under the lock, release for the API call, reclaim in ``finally``
        to clear ``loading_more`` even on error.
        """
        async with self:
            if self.loading_more or not self.next_cursor:
                return
            self.loading_more = True
            cursor = self.next_cursor
            page_size = self.page_size
        try:
            res = await api.list_templates(cursor=cursor, limit=page_size)
            raw_list = res.get("templates", [])
            if not isinstance(raw_list, list):
                raw_list = []
            new_rows = [_template_to_display(t) for t in raw_list]
            async with self:
                self.templates = list(self.templates) + new_rows
                self.next_cursor = res.get("next_cursor") or ""
                self.api_total_count = int(res.get("total_count") or self.api_total_count)
        except Exception as e:
            async with self:
                self.error = f"Failed to load more templates: {e}"
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
        yield TemplatesState.load

    # ── Filter ───────────────────────────────────────────────────────────────

    @rx.event
    def set_filter(self, f: str):
        self.active_filter = f

    @rx.event
    def set_search_text(self, value: str) -> None:
        self.search_text = value

    @rx.event
    async def set_provider_filter(self, value: str) -> None:
        """Update the active provider filter and reload the template list."""
        self.provider_filter = value
        await self.load()  # type: ignore[misc]

    # ── Auto-refresh events ──────────────────────────────────────────────────

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

    _poll_started: bool = False

    @rx.event(background=True)
    async def auto_refresh(self) -> None:
        """Background polling task driven by auto_refresh_enabled and auto_refresh_interval.

        MUST be ``background=True``. A non-background ``@rx.event`` that
        ``await``s holds the state lock for the duration of the sleep,
        which blocks every other event handler on this state (drawer
        clicks, filter switches, etc.) for the full polling interval.

        Single-flight via ``_poll_started`` so re-mounting the page
        does not spawn additional background loops.
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
                try:
                    import datetime as _dt

                    res = await api.list_templates(limit=page_size)
                    raw_list = res.get("templates", [])
                    if not isinstance(raw_list, list):
                        raw_list = []
                    async with self:
                        self.templates = [_template_to_display(t) for t in raw_list]
                        self.next_cursor = res.get("next_cursor") or ""
                        self.api_total_count = int(res.get("total_count") or len(raw_list))
                        self.last_refresh = _dt.datetime.now().strftime("%H:%M:%S")
                except Exception as e:
                    async with self:
                        self.error = f"Auto-refresh failed: {e}"
        finally:
            async with self:
                self._poll_started = False

    # ── Generate dialog ──────────────────────────────────────────────────────

    generate_dialog_open: bool = False
    generate_force: bool = False
    generating: bool = False
    generate_error: str = ""
    generate_result: str = ""

    @rx.event
    def open_generate_dialog(self):
        self.generate_dialog_open = True
        self.generate_error = ""
        self.generate_result = ""

    @rx.event
    def close_generate_dialog(self):
        self.generate_dialog_open = False

    @rx.event
    def toggle_generate_force(self, checked: bool):
        self.generate_force = checked

    @rx.event(background=True)
    async def do_generate(self):
        async with self:
            self.generating = True
            self.generate_error = ""
            self.generate_result = ""
        try:
            result = await api.generate_templates(
                {"all_providers": True, "force": self.generate_force}
            )
            async with self:
                if result.get("status") == "error":
                    self.generate_error = result.get("message") or "Generation failed"
                else:
                    total = result.get("total_templates", 0)
                    created = result.get("created_count", 0)
                    self.generate_result = (
                        f"Generated {total} template(s) across {created} provider file(s)."
                    )
                    self.generate_dialog_open = False
                await self.load()  # type: ignore[misc]
        except Exception as exc:
            async with self:
                self.generate_error = str(exc)
        finally:
            async with self:
                self.generating = False


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def _provider_badge(provider_api: str) -> rx.Component:
    """Colored badge for provider_api values."""
    return rx.badge(
        provider_api,
        variant="soft",
        color_scheme=rx.match(
            provider_api,
            ("SpotFleet", "purple"),
            ("EC2Fleet", "blue"),
            ("RunInstances", "teal"),
            ("ASG", "orange"),
            ("aws", "indigo"),
            "gray",
        ),
        size="1",
    )


def _template_card(row) -> rx.Component:
    """A clickable card consuming a pre-formatted row from
    TemplatesState.sorted_rows (Vars-safe)."""
    return rx.box(
        rx.vstack(
            # ── Top row: badge + user tag ────────────────────────────────
            rx.hstack(
                rx.badge(
                    row["provider_api"],
                    variant="soft",
                    color_scheme=row["badge_color"],
                    size="1",
                ),
                rx.cond(
                    row["has_created_by"],
                    rx.badge("user", variant="outline", size="1", color_scheme="blue"),
                    rx.fragment(),
                ),
                rx.spacer(),
                spacing="2",
                align="center",
                width="100%",
            ),
            # ── Name / ID ────────────────────────────────────────────────
            rx.text(
                rx.cond(row["name"] != "", row["name"], row["template_id"]),
                size="3",
                weight="medium",
                color=rx.color("gray", 12),
                white_space="nowrap",
                overflow="hidden",
                text_overflow="ellipsis",
                width="100%",
            ),
            rx.code(
                row["template_id"],
                size="1",
                color=rx.color("gray", 9),
                white_space="nowrap",
                overflow="hidden",
                text_overflow="ellipsis",
                width="100%",
            ),
            rx.divider(),
            # ── Stats row ────────────────────────────────────────────────
            rx.hstack(
                rx.vstack(
                    rx.tooltip(
                        rx.text(
                            row["instance_type"],
                            size="1",
                            color=rx.color("gray", 11),
                            white_space="nowrap",
                            overflow="hidden",
                            text_overflow="ellipsis",
                            max_width="10rem",
                        ),
                        content=row["instance_type"],
                    ),
                    rx.text("Instance Type(s)", size="1", color=rx.color("gray", 10)),
                    spacing="0",
                    align="center",
                ),
                rx.vstack(
                    rx.code(
                        row["image_id"],
                        size="1",
                        white_space="nowrap",
                        overflow="hidden",
                        text_overflow="ellipsis",
                        max_width="7rem",
                    ),
                    rx.text("Image", size="1", color=rx.color("gray", 10)),
                    spacing="0",
                    align="center",
                ),
                rx.vstack(
                    rx.text(row["max_instances"], size="2", weight="medium"),
                    rx.text("Max", size="1", color=rx.color("gray", 10)),
                    spacing="0",
                    align="center",
                ),
                justify="between",
                width="100%",
            ),
            # ── Actions ──────────────────────────────────────────────────
            rx.flex(
                rx.button(
                    rx.icon("send", size=14),
                    "Request",
                    size="1",
                    color_scheme="blue",
                    on_click=RequestModalState.open_for(row["template_id"]),
                ),
                rx.button(
                    rx.icon("eye", size=14),
                    "Details",
                    size="1",
                    variant="soft",
                    color_scheme="gray",
                    on_click=TemplatesState.open_drawer(row["raw"]),
                ),
                rx.button(
                    rx.icon("pencil", size=14),
                    "Edit",
                    size="1",
                    variant="soft",
                    color_scheme="gray",
                    on_click=TemplatesState.open_edit(row["raw"]),
                ),
                rx.button(
                    rx.icon("trash-2", size=14),
                    size="1",
                    variant="soft",
                    color_scheme="red",
                    on_click=TemplatesState.open_delete_for(row["raw"]),
                ),
                wrap="wrap",
                align="center",
                gap="0.375rem",
                width="100%",
            ),
            spacing="2",
            align="start",
            width="100%",
        ),
        padding="1rem",
        background=rx.color("gray", 1),
        border=f"1px solid {rx.color('gray', 5)}",
        border_radius="0.5rem",
        _hover={
            "border_color": rx.color("blue", 7),
            "box_shadow": f"0 0 0 1px {rx.color('blue', 7)}",
        },
        width="100%",
    )


def _loading_skeleton() -> rx.Component:
    """Pulsing placeholder cards while loading."""
    return rx.grid(
        *[
            rx.box(
                height="11rem",
                background=rx.color("gray", 3),
                border_radius="0.5rem",
                width="100%",
                animation="pulse 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite",
            )
            for _ in range(6)
        ],
        columns="3",
        spacing="3",
        width="100%",
    )


def _empty_state() -> rx.Component:
    return rx.vstack(
        rx.icon("file-text", size=40, color=rx.color("gray", 8)),
        rx.text("No templates found", size="4", weight="medium", color=rx.color("gray", 11)),
        rx.cond(
            TemplatesState.active_filter != "All",
            rx.vstack(
                rx.text(
                    "No templates match the current filter.",
                    size="2",
                    color=rx.color("gray", 10),
                ),
                rx.button(
                    "Show all",
                    variant="soft",
                    size="2",
                    on_click=TemplatesState.set_filter("All"),
                ),
                spacing="2",
                align="center",
            ),
            rx.vstack(
                rx.text(
                    "No templates have been created yet.",
                    size="2",
                    color=rx.color("gray", 10),
                ),
                rx.button(
                    rx.icon("plus", size=16),
                    "Create Template",
                    on_click=TemplatesState.open_create,
                    size="2",
                ),
                spacing="2",
                align="center",
            ),
        ),
        spacing="3",
        align="center",
        padding="4rem 2rem",
        width="100%",
    )


def _filter_row() -> rx.Component:
    """Canonical filter row: provider pills + provider select + search input + refresh_control.

    Layout:
        [Provider pills] [Provider dropdown] [Search input] <spacer> [refresh_control]

    The pill filter (``active_filter``) works against the static ``PROVIDER_FILTER_OPTIONS``
    list already present in the React PoC.  The new provider dropdown
    (``provider_filter``) is driven from live backend schemas and lets users scope
    the table to a specific registered provider's machines/requests.
    """
    provider_options = rx.Var.create(["All"]) + AppState.provider_schemas.keys().to(list)  # type: ignore[attr-defined]
    return rx.hstack(
        rx.foreach(
            PROVIDER_FILTER_OPTIONS,
            lambda f: rx.button(
                f,
                size="2",
                variant=rx.cond(
                    TemplatesState.active_filter == f,
                    "solid",
                    "soft",
                ),
                color_scheme=rx.cond(
                    TemplatesState.active_filter == f,
                    "blue",
                    "gray",
                ),
                on_click=TemplatesState.set_filter(f),
                radius="full",
            ),
        ),
        rx.select(
            provider_options,
            value=TemplatesState.provider_filter,
            on_change=TemplatesState.set_provider_filter,
            size="2",
            width="130px",
            placeholder="Provider…",
        ),
        rx.input(
            placeholder="Search…",
            value=TemplatesState.search_text,
            on_change=TemplatesState.set_search_text,
            width="240px",
        ),
        rx.spacer(),
        refresh_control(
            enabled=TemplatesState.auto_refresh_enabled,
            interval=TemplatesState.auto_refresh_interval,
            on_toggle=TemplatesState.toggle_auto_refresh,
            on_set_interval=TemplatesState.set_auto_refresh_interval,
            on_manual_refresh=TemplatesState.refresh,
            last_refresh_text=TemplatesState.last_refresh,
            loading=TemplatesState.loading,
        ),
        spacing="2",
        flex_wrap="wrap",
        align="center",
        margin_bottom="1rem",
        width="100%",
    )


def _generate_dialog() -> rx.Component:
    """Confirmation dialog for the Generate Examples flow."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Generate Example Templates"),
            rx.dialog.description(
                "Generate example template files for all enabled providers. "
                "By default existing files are skipped (idempotent). "
                "Enable force-overwrite to replace them.",
                size="2",
                margin_bottom="1rem",
            ),
            # Force-overwrite checkbox
            rx.flex(
                rx.checkbox(
                    checked=TemplatesState.generate_force,
                    on_change=TemplatesState.toggle_generate_force,
                ),
                rx.text("Force overwrite existing files", size="2"),
                spacing="2",
                align="center",
                margin_bottom="1rem",
            ),
            # Error callout
            rx.cond(
                TemplatesState.generate_error != "",
                rx.callout.root(
                    rx.callout.icon(rx.icon("triangle-alert", size=14)),
                    rx.callout.text(TemplatesState.generate_error),
                    color_scheme="red",
                    variant="surface",
                    margin_bottom="1rem",
                ),
                rx.fragment(),
            ),
            # Success banner
            rx.cond(
                TemplatesState.generate_result != "",
                rx.callout.root(
                    rx.callout.icon(rx.icon("check", size=14)),
                    rx.callout.text(TemplatesState.generate_result),
                    color_scheme="green",
                    variant="surface",
                    margin_bottom="1rem",
                ),
                rx.fragment(),
            ),
            # Action buttons
            rx.flex(
                rx.dialog.close(
                    rx.button(
                        "Cancel",
                        variant="soft",
                        color_scheme="gray",
                        on_click=TemplatesState.close_generate_dialog,
                    ),
                ),
                rx.button(
                    rx.icon("sparkles", size=14),
                    "Generate",
                    loading=TemplatesState.generating,
                    on_click=TemplatesState.do_generate,
                ),
                spacing="3",
                justify="end",
            ),
        ),
        open=TemplatesState.generate_dialog_open,
        on_open_change=TemplatesState.close_generate_dialog,
    )


def _toolbar() -> rx.Component:
    """Toolbar: count badge + spacer + primary action buttons + view toggle + column picker."""
    return rx.hstack(
        # Count badge (left): "Showing X of Y templates"
        rx.hstack(
            rx.text("Showing", size="2", color=rx.color("gray", 11)),
            rx.badge(
                TemplatesState.loaded_count.to_string()
                + " of "
                + TemplatesState.total_count.to_string(),
                variant="soft",
                color_scheme="gray",
                size="1",
            ),
            rx.text("templates", size="2", color=rx.color("gray", 11)),
            rx.select(
                ["50", "100", "200", "500"],
                value=TemplatesState.page_size.to_string(),
                on_change=TemplatesState.set_page_size,
                size="1",
                width="80px",
            ),
            rx.text("per page", size="2", color=rx.color("gray", 11)),
            spacing="2",
            align="center",
        ),
        rx.spacer(),
        # Primary action buttons + view controls (right)
        rx.flex(
            rx.button(
                rx.icon("plus", size=14),
                "Add Template",
                size="2",
                on_click=TemplatesState.open_create,
            ),
            rx.button(
                rx.icon("sparkles", size=14),
                "Generate Examples",
                size="2",
                variant="soft",
                color_scheme="violet",
                loading=TemplatesState.generating,
                on_click=TemplatesState.open_generate_dialog,
                title="Generate example templates for all enabled providers",
            ),
            # View toggle and column picker
            view_toggle(
                mode=TemplatesState.view_mode,
                on_change=TemplatesState.set_view_mode,
            ),
            column_picker(
                columns=TEMPLATE_COLUMNS,
                visible_columns=TemplatesState.visible_columns,
                on_toggle=TemplatesState.toggle_column,
            ),
            flex_wrap="wrap",
            row_gap="0.5rem",
            gap="0.5rem",
            align="center",
        ),
        align="center",
        width="100%",
        margin_bottom="1rem",
        flex_wrap="wrap",
        row_gap="0.5rem",
    )


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------


def templates_page() -> rx.Component:
    """Main Templates page component."""
    return page(
        "Templates",
        list_page_shell(
            error_banner=rx.cond(
                TemplatesState.error != "",
                error_callout(TemplatesState.error, retry=TemplatesState.load),
                rx.fragment(),
            ),
            banners=[request_success_banner()],
            filter_row=_filter_row(),
            toolbar=_toolbar(),
            grid=list_grid_view(
                rows=TemplatesState.sorted_rows,
                columns=TEMPLATE_COLUMNS,
                view_mode=TemplatesState.view_mode,
                visible_columns=TemplatesState.visible_columns,
                sort_key=TemplatesState.sort_key,
                sort_dir=TemplatesState.sort_dir,
                card_renderer=_template_card,
                # Row click disabled (matches machines/requests pages).
                on_row_click=None,
                on_sort=TemplatesState.set_sort,
            ),
            next_cursor=TemplatesState.next_cursor,
            loading_more=TemplatesState.loading_more,
            on_load_more=TemplatesState.load_more,
            empty=_empty_state(),
            is_loading=TemplatesState.loading & (TemplatesState.loaded_count == 0),
            is_empty=TemplatesState.filtered_count == 0,
            loading_skeleton=_loading_skeleton(),
            dialogs=[
                template_drawer(TemplatesState),
                delete_confirm_dialog(TemplatesState),
                template_form(TemplatesState),
                request_modal(),
                _generate_dialog(),
            ],
        ),
        on_mount=[
            TemplatesState.load,
            TemplatesState.auto_refresh,
        ],
    )
