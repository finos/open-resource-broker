"""Config page — server info, health checks, system details and live config editor."""

from __future__ import annotations

import os
from typing import Any

import httpx
import reflex as rx

from .. import api
from ..components.empty_state import empty_state
from ..components.error_callout import error_callout
from ..components.layout import page
from ..components.list_grid_view import ColumnDef, list_grid_view
from ..components.list_page_shell import list_page_shell
from ..state import AppState

_ORB_BASE_URL = os.getenv("ORB_BASE_URL", "in-process")


# ---------------------------------------------------------------------------
# AdminState — danger zone actions
# ---------------------------------------------------------------------------


class AdminState(rx.State):
    """State for destructive admin actions on the config page."""

    # ── Wipe ──────────────────────────────────────────────────────────────────
    wipe_confirm_input: str = ""
    wipe_dialog_open: bool = False
    wipe_in_progress: bool = False
    wipe_error: str = ""
    wipe_success: str = ""

    @rx.var
    def wipe_confirm_valid(self) -> bool:
        """True only when the user has typed the exact confirmation string."""
        return self.wipe_confirm_input == "WIPE"

    @rx.event
    def open_wipe_dialog(self):
        """Open the wipe confirmation dialog and reset its state."""
        self.wipe_confirm_input = ""
        self.wipe_error = ""
        self.wipe_success = ""
        self.wipe_dialog_open = True

    @rx.event
    def close_wipe_dialog(self):
        """Close the wipe confirmation dialog."""
        self.wipe_dialog_open = False
        self.wipe_confirm_input = ""

    @rx.event
    def set_wipe_confirm_input(self, value: str):
        """Update the confirmation input field."""
        self.wipe_confirm_input = value

    @rx.event(background=True)
    async def do_wipe(self):
        """Call the wipe endpoint and surface success or error inline."""
        async with self:
            self.wipe_in_progress = True
            self.wipe_error = ""
            self.wipe_success = ""
        try:
            result = await api.wipe_database()
            async with self:
                rows = result.get("rows_deleted", 0)
                tables = ", ".join(result.get("tables_truncated", []))
                self.wipe_success = f"Database wiped: {rows} row(s) deleted from [{tables}]."
                self.wipe_dialog_open = False
                self.wipe_confirm_input = ""
        except httpx.HTTPStatusError as exc:
            async with self:
                try:
                    body = exc.response.json()
                    detail = body.get("detail") or body
                    if isinstance(detail, dict):
                        self.wipe_error = detail.get("message", str(exc))
                    else:
                        self.wipe_error = str(detail)
                except Exception:
                    self.wipe_error = f"HTTP {exc.response.status_code}: {exc}"
        except Exception as exc:
            async with self:
                self.wipe_error = str(exc)
        finally:
            async with self:
                self.wipe_in_progress = False

    # ── Initialize ────────────────────────────────────────────────────────────
    init_dialog_open: bool = False
    init_in_progress: bool = False
    init_error: str = ""
    init_success: str = ""
    init_confirm_input: str = ""
    init_force: bool = False

    @rx.var
    def init_confirm_valid(self) -> bool:
        """True only when the user has typed the exact confirmation string."""
        return self.init_confirm_input == "INIT"

    @rx.event
    def open_init_dialog(self):
        """Open the init confirmation dialog and reset its state."""
        self.init_confirm_input = ""
        self.init_error = ""
        self.init_success = ""
        self.init_force = False
        self.init_dialog_open = True

    @rx.event
    def close_init_dialog(self):
        """Close the init confirmation dialog."""
        self.init_dialog_open = False
        self.init_confirm_input = ""

    @rx.event
    def set_init_confirm_input(self, value: str):
        """Update the init confirmation input field."""
        self.init_confirm_input = value

    @rx.event
    def toggle_init_force(self, value: bool):
        """Toggle force-overwrite checkbox."""
        self.init_force = value

    @rx.event(background=True)
    async def do_init(self):
        """Call the init endpoint and surface success or error inline."""
        async with self:
            self.init_in_progress = True
            self.init_error = ""
            self.init_success = ""
        try:
            result = await api.init_orb(
                {
                    "confirm": "INIT",
                    "force": self.init_force,
                    "generate_templates": True,
                }
            )
            async with self:
                templates_n = result.get("templates_generated", 0)
                files_n = len(result.get("created_files", []))
                dirs_n = len(result.get("created_dirs", []))
                parts = []
                if files_n:
                    parts.append(f"{files_n} config file(s) created")
                if dirs_n:
                    parts.append(f"{dirs_n} director{'y' if dirs_n == 1 else 'ies'} created")
                parts.append(f"{templates_n} template(s) generated")
                self.init_success = "Initialized ORB — " + ", ".join(parts) + "."
                self.init_dialog_open = False
                self.init_confirm_input = ""
        except httpx.HTTPStatusError as exc:
            async with self:
                try:
                    body = exc.response.json()
                    detail = body.get("detail") or body
                    if isinstance(detail, dict):
                        self.init_error = detail.get("message", str(exc))
                    else:
                        self.init_error = str(detail)
                except Exception:
                    self.init_error = f"HTTP {exc.response.status_code}: {exc}"
        except Exception as exc:
            async with self:
                self.init_error = str(exc)
        finally:
            async with self:
                self.init_in_progress = False


# ---------------------------------------------------------------------------
# ConfigState — live configuration editor
# ---------------------------------------------------------------------------


class ConfigState(rx.State):
    """State for the live configuration editor section."""

    config: dict[str, Any] = {}
    # Raw on-disk config dict (before Pydantic hydration).
    config_file: dict[str, Any] = {}
    source_file: str = ""
    last_reloaded: str = ""
    loading: bool = False
    error: str = ""
    editing_key: str = ""
    edit_buffer: str = ""
    save_in_progress: bool = False
    save_note: str = ""

    # ── Source filter (All / File / Defaults) ─────────────────────────────────
    source_filter: str = rx.LocalStorage("all", name="orb-config-source-filter")

    # ── Key-path search ───────────────────────────────────────────────────────
    search_query: str = ""

    @rx.var
    def section_names(self) -> list[str]:
        """Top-level config section names (e.g. server, storage, naming)."""
        return sorted(self.config.keys()) if isinstance(self.config, dict) else []

    # -----------------------------------------------------------------------
    # Internal helper — reused by both flat_rows_file and flat_rows_defaults
    # -----------------------------------------------------------------------

    def _flatten(self, cfg: dict[str, Any]) -> dict[str, dict[str, str]]:
        """Flatten a two-level config dict → {dotted_key: row_dict}.

        Each value dict has the same shape as a flat_rows entry so callers
        can extend it with an ``origin`` key before appending.
        """
        import json as _json

        result: dict[str, dict[str, str]] = {}
        if not isinstance(cfg, dict):
            return result
        for section in sorted(cfg.keys()):
            sub = cfg.get(section)
            if isinstance(sub, dict):
                for leaf_name in sorted(sub.keys()):
                    leaf_val = sub[leaf_name]
                    if isinstance(leaf_val, (str, int, float, bool)) or leaf_val is None:
                        rendered = "" if leaf_val is None else str(leaf_val)
                    else:
                        try:
                            rendered = _json.dumps(leaf_val, default=str)
                        except Exception:
                            rendered = str(leaf_val)
                    dotted = f"{section}.{leaf_name}"
                    result[dotted] = {
                        "section": section,
                        "key": dotted,
                        "leaf": leaf_name,
                        "value": rendered,
                        "editable": "1" if isinstance(leaf_val, (str, int, float, bool)) else "0",
                    }
            else:
                rendered = "" if sub is None else str(sub)
                result[section] = {
                    "section": section,
                    "key": section,
                    "leaf": section,
                    "value": rendered,
                    "editable": "1" if isinstance(sub, (str, int, float, bool)) else "0",
                }
        return result

    @rx.var
    def flat_rows(self) -> list[dict[str, str]]:
        """Legacy flat row list (all rows). Kept for backwards compatibility."""
        rows = list(self._flatten(self.config).values())
        for r in rows:
            r["origin"] = "effective"
        return rows

    @rx.var
    def flat_rows_file(self) -> list[dict[str, str]]:
        """Rows whose leaf path exists in the on-disk config file.

        These are keys the operator explicitly set in their config file.
        Each row has an additional ``origin`` = ``"file"`` field.
        """
        file_keys = set(self._flatten(self.config_file).keys())
        effective = self._flatten(self.config)
        rows: list[dict[str, str]] = []
        for key, row in effective.items():
            if key in file_keys:
                r = dict(row)
                r["origin"] = "file"
                rows.append(r)
        return rows

    @rx.var
    def flat_rows_defaults(self) -> list[dict[str, str]]:
        """Rows whose leaf path is NOT in the on-disk file — compiled-in defaults.

        Each row has an additional ``origin`` = ``"default"`` field.
        """
        file_keys = set(self._flatten(self.config_file).keys())
        effective = self._flatten(self.config)
        rows: list[dict[str, str]] = []
        for key, row in effective.items():
            if key not in file_keys:
                r = dict(row)
                r["origin"] = "default"
                rows.append(r)
        return rows

    @rx.var
    def file_row_count(self) -> int:
        return len(self.flat_rows_file)

    @rx.var
    def default_row_count(self) -> int:
        return len(self.flat_rows_defaults)

    @rx.var
    def visible_rows(self) -> list[dict[str, str]]:
        """Rows visible after applying source_filter and search_query.

        source_filter logic:
          - ``"all"``      → all effective config leaves (with origin tagged)
          - ``"file"``     → only leaves present in the on-disk config file
          - ``"defaults"`` → only compiled-in defaults (not in file)

        search_query filters by key-path prefix (case-insensitive contains).
        """
        if self.source_filter == "file":
            base = self.flat_rows_file
        elif self.source_filter == "defaults":
            base = self.flat_rows_defaults
        else:
            # "all": merge file rows + default rows to get full effective set
            # with correct per-row origin tags
            base = self.flat_rows_file + self.flat_rows_defaults

        q = self.search_query.strip().lower()
        if not q:
            return base
        return [r for r in base if q in r.get("key", "").lower()]

    @rx.var
    def visible_row_count(self) -> int:
        return len(self.visible_rows)

    @rx.var
    def total_row_count(self) -> int:
        """Total effective config rows (file + defaults combined)."""
        return len(self.flat_rows_file) + len(self.flat_rows_defaults)

    # ── Filter / search events ────────────────────────────────────────────────

    @rx.event
    def set_source_filter(self, value: str) -> None:
        """Set the source filter pill (all / file / defaults)."""
        self.source_filter = value

    @rx.event
    def set_search_query(self, value: str) -> None:
        """Update the key-path search query."""
        self.search_query = value

    @rx.event(background=True)
    async def load(self):
        """Fetch both the effective config and the raw on-disk config."""
        async with self:
            self.loading = True
            self.error = ""
        try:
            config_data = await api.get_config()
            file_data = await api.get_config(source="file")
            sources = await api.get_config_sources()
            async with self:
                self.config = config_data
                self.config_file = file_data if isinstance(file_data, dict) else {}
                self.source_file = sources.get("config_file") or ""
                import datetime

                self.last_reloaded = datetime.datetime.now().strftime("%H:%M:%S")
        except Exception as exc:
            async with self:
                self.error = str(exc)
        finally:
            async with self:
                self.loading = False

    @rx.event(background=True)
    async def reload(self):
        """Reload config from disk then refresh the local state."""
        async with self:
            self.loading = True
            self.error = ""
        try:
            result = await api.reload_config()
            async with self:
                self.source_file = result.get("source") or self.source_file
            # Refresh full config after reload
            config_data = await api.get_config()
            async with self:
                self.config = config_data
                import datetime

                self.last_reloaded = datetime.datetime.now().strftime("%H:%M:%S")
        except Exception as exc:
            async with self:
                self.error = str(exc)
        finally:
            async with self:
                self.loading = False

    @rx.event(background=True)
    async def persist_to_disk(self):
        """Write the current in-memory config to disk via POST /config/save.

        After this call the on-disk file matches the in-memory state, so a
        server restart preserves the operator's edits. Surface success
        through ``save_note`` so the same banner the per-key edit uses can
        confirm the round-trip.
        """
        async with self:
            self.loading = True
            self.error = ""
            self.save_note = ""
        try:
            result = await api.save_config()
            async with self:
                path = result.get("path", "")
                self.save_note = f"Saved to disk: {path}" if path else "Saved to disk."
        except Exception as exc:
            async with self:
                self.error = f"Save failed: {exc}"
        finally:
            async with self:
                self.loading = False

    @rx.event
    def start_edit(self, key: str, current_value: str):
        """Begin editing a config key."""
        self.editing_key = key
        self.edit_buffer = current_value
        self.save_note = ""

    @rx.event
    def cancel_edit(self):
        """Cancel in-progress edit."""
        self.editing_key = ""
        self.edit_buffer = ""
        self.save_note = ""

    @rx.event
    def set_edit_buffer(self, value: str):
        """Update the edit buffer as the user types."""
        self.edit_buffer = value

    @rx.event(background=True)
    async def save_edit(self):
        """Persist the current edit buffer to in-memory config via the API."""
        async with self:
            self.save_in_progress = True
            self.save_note = ""
        try:
            key = self.editing_key
            # Coerce: try int, then float, then bool, then keep as string
            raw = self.edit_buffer
            coerced: Any = raw
            lower = raw.lower()
            if lower in ("true", "false"):
                coerced = lower == "true"
            else:
                for cast in (int, float):
                    try:
                        coerced = cast(raw)
                        break
                    except (ValueError, TypeError):
                        # Cast attempt failed — try next type (int → float → keep as string)
                        pass

            result = await api.set_config_value(key, coerced)
            async with self:
                self.save_note = result.get(
                    "note",
                    "Saved in-memory. Reload from file will revert this change.",
                )
                self.editing_key = ""
                self.edit_buffer = ""
                # Refresh the config dict so the displayed value updates
                top_key = key.split(".")[0]
                if top_key in self.config and "." in key:
                    # Best-effort: update nested value without a full reload
                    parts = key.split(".")
                    node: Any = self.config
                    for p in parts[:-1]:
                        if isinstance(node, dict):
                            node = node.get(p, {})
                        else:
                            node = {}
                            break
                    if isinstance(node, dict):
                        node[parts[-1]] = result.get("value", coerced)
                else:
                    self.config[key] = result.get("value", coerced)
        except Exception as exc:
            async with self:
                self.save_note = f"Error: {exc}"
        finally:
            async with self:
                self.save_in_progress = False


# ---------------------------------------------------------------------------
# Config table — column definitions + formatters
# ---------------------------------------------------------------------------

_ORIGIN_COLOR_MAP = {
    "file": "green",
    "default": "gray",
    "effective": "blue",
}


def _origin_badge(row: Any) -> rx.Component:
    """Render the origin column as a colour-coded badge."""
    return rx.badge(
        row["origin"],
        color_scheme=rx.match(
            row["origin"],
            ("file", "green"),
            ("default", "gray"),
            "blue",
        ),
        variant="soft",
        size="1",
    )


def _value_cell(row: Any) -> rx.Component:
    """Render value as rx.code (truncated for long strings)."""
    return rx.code(row["value"], size="1")


def _actions_cell(row: Any) -> rx.Component:
    """Inline edit icon — active only for scalar (editable) leaves."""
    return rx.cond(
        row["editable"] == "1",
        rx.tooltip(
            rx.icon_button(
                rx.icon("pencil", size=12),
                size="1",
                variant="ghost",
                color_scheme="gray",
                on_click=ConfigState.start_edit(row["key"], row["value"]),
            ),
            content="Edit (in-memory only — reload reverts)",
        ),
        rx.tooltip(
            rx.icon("lock", size=12, color=rx.color("gray", 9)),
            content="Nested value — not editable inline.",
        ),
    )


CONFIG_COLUMNS: list[ColumnDef] = [
    ColumnDef(
        "section",
        "Section",
        default_visible=True,
        lockable=True,
        width="120px",
    ),
    ColumnDef(
        "key",
        "Key",
        default_visible=True,
        lockable=True,
        sortable=True,
    ),
    ColumnDef(
        "value",
        "Value",
        default_visible=True,
        lockable=True,
        formatter=_value_cell,
    ),
    ColumnDef(
        "origin",
        "Origin",
        default_visible=True,
        lockable=True,
        formatter=_origin_badge,
        width="90px",
    ),
    ColumnDef(
        "_actions",
        "Actions",
        default_visible=True,
        lockable=True,
        formatter=_actions_cell,
        width="64px",
        align="end",
    ),
]

# visible_columns sentinel: all lockable so we just pass a fenced full string
_CONFIG_VISIBLE_DEFAULT = ",section,key,value,origin,_actions,"


def _config_card(row: Any) -> rx.Component:
    """Card renderer for the config table (grid mode)."""
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.badge(row["section"], variant="soft", color_scheme="blue", size="1"),
                rx.spacer(),
                _origin_badge(row),
                align="center",
                width="100%",
            ),
            rx.code(row["key"], size="1"),
            rx.code(row["value"], size="1", color=rx.color("gray", 11)),
            rx.cond(
                row["editable"] == "1",
                rx.icon_button(
                    rx.icon("pencil", size=12),
                    size="1",
                    variant="ghost",
                    color_scheme="gray",
                    on_click=ConfigState.start_edit(row["key"], row["value"]),
                ),
                rx.fragment(),
            ),
            spacing="2",
            width="100%",
        ),
        width="100%",
    )


# ---------------------------------------------------------------------------
# Config section UI helpers (server / health / system panels — unchanged)
# ---------------------------------------------------------------------------


def _section_box(*children: rx.Component) -> rx.Component:
    return rx.box(
        rx.vstack(
            *children,
            spacing="3",
            align="start",
            width="100%",
        ),
        padding="1.5rem",
        background=rx.color("gray", 2),
        border_radius="0.5rem",
        border=f"1px solid {rx.color('gray', 5)}",
        width="100%",
        max_width="640px",
    )


def _row(label: str, value: Any) -> rx.Component:
    """Labeled read-only row."""
    return rx.hstack(
        rx.text(label, size="2", color=rx.color("gray", 11), min_width="180px"),
        rx.code(value, size="2"),
        spacing="3",
        align="center",
        width="100%",
    )


def _health_check_row_var(row: Any) -> rx.Component:
    """Render one health-check row from AppState.health_check_rows (typed)."""
    return rx.hstack(
        rx.text(
            row["name"],
            size="2",
            color=rx.color("gray", 11),
            min_width="180px",
        ),
        rx.badge(
            row["status"],
            color_scheme=rx.match(
                row["status"],
                ("ok", "green"),
                ("healthy", "green"),
                ("pass", "green"),
                ("passed", "green"),
                ("unhealthy", "red"),
                ("fail", "red"),
                ("failed", "red"),
                "amber",
            ),
            variant="soft",
            size="1",
        ),
        rx.cond(
            row["message"] != "",
            rx.text(row["message"], size="1", color=rx.color("gray", 10)),
            rx.fragment(),
        ),
        spacing="3",
        align="center",
        width="100%",
    )


def _health_checks_section() -> rx.Component:
    """Render per-component health checks via foreach over typed rows."""
    return rx.foreach(AppState.health_check_rows, _health_check_row_var)


# ---------------------------------------------------------------------------
# Config table sub-components (filter row / toolbar / edit overlay)
# ---------------------------------------------------------------------------

_SOURCE_FILTERS: list[tuple[str, str]] = [
    ("all", "All"),
    ("file", "File"),
    ("defaults", "Defaults"),
]


def _config_filter_row() -> rx.Component:
    """Filter row: source pills + search input + reload button (right)."""
    return rx.hstack(
        # Source filter pills
        rx.hstack(
            *[
                rx.button(
                    label,
                    size="2",
                    variant=rx.cond(ConfigState.source_filter == value, "solid", "soft"),
                    color_scheme=rx.cond(ConfigState.source_filter == value, "blue", "gray"),
                    radius="full",
                    on_click=ConfigState.set_source_filter(value),
                    role="tab",
                    aria_selected=rx.cond(ConfigState.source_filter == value, "true", "false"),
                )
                for value, label in _SOURCE_FILTERS
            ],
            role="tablist",
            spacing="2",
            align="center",
        ),
        # Key-path search
        rx.input(
            placeholder="Search key…",
            value=ConfigState.search_query,
            on_change=ConfigState.set_search_query,
            width="240px",
        ),
        rx.spacer(),
        # Reload / save buttons
        rx.hstack(
            rx.button(
                rx.icon("save", size=14),
                "Save to disk",
                on_click=ConfigState.persist_to_disk,
                variant="solid",
                color_scheme="blue",
                disabled=ConfigState.loading,
                size="2",
                title="Write the in-memory config to the loaded config file",
            ),
            rx.button(
                rx.cond(
                    ConfigState.loading,
                    rx.spinner(size="2"),
                    rx.icon("refresh-cw", size=14),
                ),
                "Reload",
                on_click=ConfigState.reload,
                variant="soft",
                disabled=ConfigState.loading,
                size="2",
                title="Discard in-memory edits and re-read from disk",
            ),
            spacing="2",
            align="center",
        ),
        spacing="2",
        flex_wrap="wrap",
        align="center",
        margin_bottom="1rem",
        width="100%",
    )


def _config_toolbar() -> rx.Component:
    """Toolbar: Showing X of Y badge + source file info (right)."""
    return rx.hstack(
        # Count badge (left)
        rx.hstack(
            rx.text("Showing", size="2", color=rx.color("gray", 11)),
            rx.badge(
                ConfigState.visible_row_count.to_string()
                + " of "
                + ConfigState.total_row_count.to_string(),
                variant="soft",
                color_scheme="gray",
                size="1",
            ),
            rx.text("keys", size="2", color=rx.color("gray", 11)),
            spacing="2",
            align="center",
        ),
        rx.spacer(),
        # Source file path (right)
        rx.cond(
            ConfigState.source_file != "",
            rx.hstack(
                rx.text("Source:", size="2", color=rx.color("gray", 11)),
                rx.code(ConfigState.source_file, size="1"),
                spacing="2",
                align="center",
            ),
            rx.fragment(),
        ),
        rx.cond(
            ConfigState.last_reloaded != "",
            rx.text(
                "Last reloaded: ",
                rx.text.span(ConfigState.last_reloaded, font_weight="500"),
                size="2",
                color=rx.color("gray", 10),
            ),
            rx.fragment(),
        ),
        align="center",
        margin_bottom="1rem",
        width="100%",
        flex_wrap="wrap",
        row_gap="0.5rem",
        spacing="2",
    )


def _edit_overlay() -> rx.Component:
    """Floating edit bar shown while a key is being edited."""
    return rx.cond(
        ConfigState.editing_key != "",
        rx.box(
            rx.hstack(
                rx.text(
                    "Editing: ",
                    rx.code(ConfigState.editing_key, size="2"),
                    size="2",
                    color=rx.color("gray", 11),
                ),
                rx.input(
                    value=ConfigState.edit_buffer,
                    on_change=ConfigState.set_edit_buffer,
                    size="1",
                    width="300px",
                    placeholder="New value…",
                ),
                rx.button(
                    rx.cond(
                        ConfigState.save_in_progress,
                        rx.spinner(size="1"),
                        rx.icon("check", size=12),
                    ),
                    "Save",
                    size="1",
                    variant="solid",
                    color_scheme="green",
                    on_click=ConfigState.save_edit,
                    disabled=ConfigState.save_in_progress,
                ),
                rx.button(
                    rx.icon("x", size=12),
                    "Cancel",
                    size="1",
                    variant="ghost",
                    color_scheme="gray",
                    on_click=ConfigState.cancel_edit,
                ),
                spacing="2",
                align="center",
                flex_wrap="wrap",
            ),
            padding="0.75rem 1rem",
            background=rx.color("amber", 2),
            border_radius="0.5rem",
            border=f"1px solid {rx.color('amber', 6)}",
            width="100%",
            max_width="860px",
        ),
        rx.fragment(),
    )


def _config_table_section() -> rx.Component:
    """Config table using list_page_shell + list_grid_view."""
    return rx.vstack(
        rx.heading("Configuration", size="5", margin_bottom="0.5rem"),
        # Save note / error banners above the shell
        rx.cond(
            ConfigState.save_note != "",
            rx.callout(
                ConfigState.save_note,
                icon="info",
                color_scheme="amber",
                size="1",
                max_width="860px",
            ),
            rx.fragment(),
        ),
        _edit_overlay(),
        list_page_shell(
            error_banner=rx.cond(
                ConfigState.error != "",
                error_callout(ConfigState.error, retry=ConfigState.load),
                rx.fragment(),
            ),
            filter_row=_config_filter_row(),
            toolbar=_config_toolbar(),
            grid=list_grid_view(
                rows=ConfigState.visible_rows,
                columns=CONFIG_COLUMNS,
                view_mode=rx.Var.create("list"),
                visible_columns=rx.Var.create(_CONFIG_VISIBLE_DEFAULT),
                sort_key=rx.Var.create(""),
                sort_dir=rx.Var.create("asc"),
                card_renderer=_config_card,
                on_row_click=None,
                on_sort=None,
            ),
            load_more=rx.fragment(),
            empty=empty_state(
                icon="settings",
                title="No configuration keys found",
                description="Try adjusting the source filter or search query.",
            ),
            is_loading=ConfigState.loading,
            is_empty=ConfigState.visible_row_count == 0,
        ),
        spacing="3",
        align="start",
        width="100%",
        on_mount=ConfigState.load,
    )


# ---------------------------------------------------------------------------
# Config page entry point
# ---------------------------------------------------------------------------


def config_page() -> rx.Component:
    return page(
        "Configuration",
        # ── Ephemeral-edits banner ─────────────────────────────────────────
        rx.callout(
            rx.text(
                "Config edits apply to the running process only. "
                'Click "Save to disk" to persist changes across restarts, '
                'or "Reload" to discard them.',
                size="2",
            ),
            icon="alert-triangle",
            color_scheme="amber",
            variant="surface",
            size="1",
            width="100%",
            max_width="860px",
            margin_bottom="1rem",
        ),
        # ── Config table (list_page_shell) ─────────────────────────────────
        _config_table_section(),
        # ── Server section ─────────────────────────────────────────────────
        rx.vstack(
            rx.heading("Server", size="5", margin_bottom="0.5rem"),
            _section_box(
                _row(
                    "Service",
                    AppState.info.get("service", "—"),
                ),
                _row(
                    "Version",
                    AppState.info.get("version", "—"),
                ),
                _row(
                    "Description",
                    AppState.info.get("description", "—"),
                ),
                _row(
                    "Auth enabled",
                    AppState.info.get("auth_enabled", False).to_string(),
                ),
                _row(
                    "Auth strategy",
                    AppState.info.get("auth_strategy", "none"),
                ),
            ),
            # ── Health section ─────────────────────────────────────────────
            rx.heading("Health", size="5", margin_top="1rem", margin_bottom="0.5rem"),
            _section_box(
                rx.hstack(
                    rx.text("Overall", size="2", color=rx.color("gray", 11), min_width="180px"),
                    rx.badge(
                        AppState.health.get("status", "unknown"),
                        color_scheme=AppState.server_status_color,
                        variant="soft",
                        size="1",
                    ),
                    spacing="3",
                    align="center",
                    width="100%",
                ),
                rx.divider(),
                _health_checks_section(),
            ),
            # ── System section ─────────────────────────────────────────────
            rx.heading("System", size="5", margin_top="1rem", margin_bottom="0.5rem"),
            _section_box(
                _row("API base URL", _ORB_BASE_URL),
                _row("ORB mode", api.mode()),
            ),
            # Refresh button
            rx.button(
                rx.icon("refresh-cw", size=14),
                "Refresh health",
                on_click=AppState.poll_health,
                variant="soft",
                margin_top="0.5rem",
            ),
            spacing="0",
            align="start",
            width="100%",
        ),
        # ── Danger Zone ────────────────────────────────────────────────────
        rx.vstack(
            rx.heading(
                "Danger Zone",
                size="5",
                color=rx.color("red", 11),
                margin_top="2rem",
                margin_bottom="0.5rem",
            ),
            rx.box(
                rx.vstack(
                    rx.heading(
                        "Destructive Actions",
                        size="3",
                        color=rx.color("red", 11),
                    ),
                    rx.text(
                        "These actions are irreversible. All data will be permanently deleted.",
                        size="2",
                        color=rx.color("gray", 11),
                    ),
                    rx.divider(),
                    rx.hstack(
                        rx.vstack(
                            rx.text(
                                "Wipe Database",
                                size="2",
                                weight="bold",
                            ),
                            rx.text(
                                "Delete all machines, requests, return requests, "
                                "and templates from the database. The schema and "
                                "application remain running — only row data is removed.",
                                size="2",
                                color=rx.color("gray", 11),
                            ),
                            align="start",
                            spacing="1",
                        ),
                        rx.button(
                            rx.icon("trash-2", size=14),
                            "Wipe Database",
                            color_scheme="red",
                            variant="solid",
                            on_click=AdminState.open_wipe_dialog,
                        ),
                        align="center",
                        justify="between",
                        width="100%",
                    ),
                    # Inline feedback — wipe
                    rx.cond(
                        AdminState.wipe_success != "",
                        rx.callout(
                            AdminState.wipe_success,
                            icon="check",
                            color_scheme="green",
                            size="1",
                        ),
                        rx.fragment(),
                    ),
                    rx.cond(
                        AdminState.wipe_error != "",
                        rx.callout(
                            AdminState.wipe_error,
                            icon="alert-triangle",
                            color_scheme="red",
                            size="1",
                        ),
                        rx.fragment(),
                    ),
                    rx.divider(),
                    # ── Initialize ORB ──────────────────────────────────────
                    rx.hstack(
                        rx.vstack(
                            rx.text(
                                "Initialize ORB",
                                size="2",
                                weight="bold",
                            ),
                            rx.text(
                                "Create the default config file, data directories, "
                                "and refresh example templates. Safe to run after a "
                                "wipe or on a fresh install.",
                                size="2",
                                color=rx.color("gray", 11),
                            ),
                            align="start",
                            spacing="1",
                        ),
                        rx.button(
                            rx.icon("play-circle", size=14),
                            "Initialize ORB",
                            color_scheme="green",
                            variant="solid",
                            on_click=AdminState.open_init_dialog,
                        ),
                        align="center",
                        justify="between",
                        width="100%",
                    ),
                    # Inline feedback — init
                    rx.cond(
                        AdminState.init_success != "",
                        rx.callout(
                            AdminState.init_success,
                            icon="check",
                            color_scheme="green",
                            size="1",
                        ),
                        rx.fragment(),
                    ),
                    rx.cond(
                        AdminState.init_error != "",
                        rx.callout(
                            AdminState.init_error,
                            icon="alert-triangle",
                            color_scheme="red",
                            size="1",
                        ),
                        rx.fragment(),
                    ),
                    spacing="3",
                    align="start",
                    width="100%",
                ),
                padding="1.5rem",
                background=rx.color("red", 2),
                border_radius="0.5rem",
                border=f"1px solid {rx.color('red', 6)}",
                width="100%",
                max_width="640px",
            ),
            spacing="0",
            align="start",
            width="100%",
        ),
        # ── Wipe confirmation dialog ───────────────────────────────────────
        rx.alert_dialog.root(
            rx.alert_dialog.content(
                rx.alert_dialog.title(
                    "Wipe entire database?",
                    color=rx.color("red", 11),
                ),
                rx.alert_dialog.description(
                    rx.vstack(
                        rx.callout(
                            "This will permanently delete ALL machines, requests, "
                            "return requests, and templates. This action cannot be undone.",
                            icon="alert-triangle",
                            color_scheme="red",
                            width="100%",
                        ),
                        rx.text(
                            "Type WIPE to confirm:",
                            size="2",
                            weight="medium",
                            margin_top="0.75rem",
                        ),
                        rx.input(
                            placeholder="Type WIPE to confirm",
                            value=AdminState.wipe_confirm_input,
                            on_change=AdminState.set_wipe_confirm_input,
                            width="100%",
                        ),
                        spacing="2",
                        align="start",
                        width="100%",
                    ),
                ),
                rx.hstack(
                    rx.alert_dialog.cancel(
                        rx.button(
                            "Cancel",
                            variant="soft",
                            color_scheme="gray",
                            on_click=AdminState.close_wipe_dialog,
                        ),
                    ),
                    rx.button(
                        rx.cond(
                            AdminState.wipe_in_progress,
                            rx.spinner(size="2"),
                            rx.icon("trash-2", size=14),
                        ),
                        rx.cond(
                            AdminState.wipe_in_progress,
                            "Wiping...",
                            "I understand, wipe everything",
                        ),
                        color_scheme="red",
                        variant="solid",
                        disabled=~AdminState.wipe_confirm_valid | AdminState.wipe_in_progress,
                        on_click=AdminState.do_wipe,
                    ),
                    justify="end",
                    spacing="2",
                    margin_top="1rem",
                ),
            ),
            open=AdminState.wipe_dialog_open,
        ),
        # ── Initialize ORB confirmation dialog ────────────────────────────
        rx.alert_dialog.root(
            rx.alert_dialog.content(
                rx.alert_dialog.title(
                    "Initialize ORB",
                    color=rx.color("green", 11),
                ),
                rx.alert_dialog.description(
                    rx.vstack(
                        rx.callout(
                            "This will create the default config file, data directories, "
                            "and refresh example templates. Existing data is not deleted.",
                            icon="info",
                            color_scheme="blue",
                            width="100%",
                        ),
                        rx.hstack(
                            rx.checkbox(
                                checked=AdminState.init_force,
                                on_change=AdminState.toggle_init_force,
                            ),
                            rx.text(
                                "Force overwrite existing config file",
                                size="2",
                            ),
                            spacing="2",
                            align="center",
                            margin_top="0.5rem",
                        ),
                        rx.text(
                            "Type INIT to confirm:",
                            size="2",
                            weight="medium",
                            margin_top="0.75rem",
                        ),
                        rx.input(
                            placeholder="Type INIT to confirm",
                            value=AdminState.init_confirm_input,
                            on_change=AdminState.set_init_confirm_input,
                            width="100%",
                        ),
                        spacing="2",
                        align="start",
                        width="100%",
                    ),
                ),
                rx.hstack(
                    rx.alert_dialog.cancel(
                        rx.button(
                            "Cancel",
                            variant="soft",
                            color_scheme="gray",
                            on_click=AdminState.close_init_dialog,
                        ),
                    ),
                    rx.button(
                        rx.cond(
                            AdminState.init_in_progress,
                            rx.spinner(size="2"),
                            rx.icon("play-circle", size=14),
                        ),
                        rx.cond(
                            AdminState.init_in_progress,
                            "Initializing...",
                            "Initialize ORB",
                        ),
                        color_scheme="green",
                        variant="solid",
                        disabled=~AdminState.init_confirm_valid | AdminState.init_in_progress,
                        on_click=AdminState.do_init,
                    ),
                    justify="end",
                    spacing="2",
                    margin_top="1rem",
                ),
            ),
            open=AdminState.init_dialog_open,
        ),
    )
