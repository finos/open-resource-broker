"""Build provider-declared ColumnDef instances from backend schema descriptors.

The backend exposes ``GET /api/v1/providers/schemas`` which returns a dict
keyed by provider name.  Each value is a list of descriptor dicts whose shape
is documented in the ORB provider contract::

    {
        "key": "aws_instance_type",
        "path": "provider_data.instance_type",
        "label": "Instance Type",
        "kind": "text|code|badge|timestamp|count|link",
        "resource_type": "machines|requests|templates",
        "provider": "aws",
        "sortable": true,
        "default_visible": true,
        "lockable": false,
        "badge_color_map": {"spot": "orange", "ondemand": "blue"}
    }

This module converts those descriptors into ``ColumnDef`` instances that the
``list_grid_view`` component can consume directly.
"""

from __future__ import annotations

from typing import Any, Literal

import reflex as rx

from .list_grid_view import ColumnDef


def _dotted_get(row: dict[str, Any], path: str) -> Any:
    """Walk *row* along *path* (dot-separated segments).

    Returns the value at the leaf or an empty string when any segment is
    missing or the intermediate value is not a dict.

    Examples::

        _dotted_get({"a": {"b": 1}}, "a.b")   # → 1
        _dotted_get({"a": {}}, "a.b")          # → ""
        _dotted_get({}, "a.b.c")              # → ""
    """
    current: Any = row
    for segment in path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(segment, "")
    return current if current is not None else ""


def _make_formatter(descriptor: dict[str, Any]):
    """Return a Reflex cell formatter for the given descriptor.

    The formatter signature is ``(row: Any) -> rx.Component`` where *row* is a
    Reflex Var representing the current row dict.  The ``path`` field is used
    to look up the value; at runtime the row dict contains pre-flattened
    string representations so we read via the ``key`` for top-level access
    and fall back to the path for nested reads.

    Kind → rendering strategy:
    - ``code``      → ``rx.code``
    - ``badge``     → ``rx.badge`` with optional ``badge_color_map`` lookup
    - ``timestamp`` → plain text (already formatted server-side or in machine_rows)
    - ``count``     → ``rx.badge`` numeric counter
    - ``link``      → ``rx.link``
    - ``text`` (default) → ``rx.text``
    """
    kind = descriptor.get("kind", "text")
    key = descriptor.get("key", "")
    badge_color_map: dict[str, str] = descriptor.get("badge_color_map") or {}

    if kind == "code":

        def _fmt(row: Any) -> rx.Component:
            val = row[key]
            return rx.cond(
                val != "",
                rx.code(val, size="1"),
                rx.text("—", size="1", color=rx.color("gray", 9)),
            )

    elif kind == "badge":
        if badge_color_map:

            def _fmt(row: Any) -> rx.Component:
                val = row[key]
                # Build a static match expression covering known values; unknown
                # values fall back to gray.  We build a Python-level default
                # formatter; badge_color_map is captured in the closure.
                color = rx.match(
                    val,
                    *[(v, c) for v, c in badge_color_map.items()],
                    "gray",
                )
                return rx.cond(
                    val != "",
                    rx.badge(val, variant="soft", color_scheme=color, size="1"),
                    rx.text("—", size="1", color=rx.color("gray", 9)),
                )

        else:

            def _fmt(row: Any) -> rx.Component:
                val = row[key]
                return rx.cond(
                    val != "",
                    rx.badge(val, variant="soft", color_scheme="gray", size="1"),
                    rx.text("—", size="1", color=rx.color("gray", 9)),
                )

    elif kind == "count":

        def _fmt(row: Any) -> rx.Component:
            val = row[key]
            return rx.cond(
                val != "",
                rx.badge(val, variant="soft", color_scheme="blue", size="1"),
                rx.text("—", size="1", color=rx.color("gray", 9)),
            )

    elif kind == "link":

        def _fmt(row: Any) -> rx.Component:
            val = row[key]
            return rx.cond(
                val != "",
                rx.link(val, href=val, is_external=True, size="1"),
                rx.text("—", size="1", color=rx.color("gray", 9)),
            )

    else:
        # text / timestamp / default

        def _fmt(row: Any) -> rx.Component:
            val = row[key]
            return rx.cond(
                val != "",
                rx.text(val, size="2"),
                rx.text("—", size="1", color=rx.color("gray", 9)),
            )

    return _fmt


def _descriptor_to_column_def(descriptor: dict[str, Any]) -> ColumnDef:
    """Convert a single backend UIColumnDescriptor dict into a ``ColumnDef``."""
    key = str(descriptor.get("key") or "")
    title = str(descriptor.get("label") or key)
    sortable = bool(descriptor.get("sortable", False))
    default_visible = bool(descriptor.get("default_visible", False))
    lockable = bool(descriptor.get("lockable", False))

    return ColumnDef(
        key=key,
        title=title,
        formatter=_make_formatter(descriptor),
        default_visible=default_visible,
        lockable=lockable,
        sortable=sortable,
    )


def build_provider_columns(
    schemas: dict[str, list[dict[str, Any]]],
    resource_type: Literal["machines", "requests", "templates"],
    active_provider: str | None,
) -> list[ColumnDef]:
    """Convert provider-declared column descriptors into ColumnDef instances.

    Args:
        schemas:         The full ``provider_schemas`` dict from ``AppState``
                         (keyed by provider name, values are descriptor lists).
        resource_type:   One of ``"machines"``, ``"requests"``, or
                         ``"templates"``.  Only descriptors whose
                         ``resource_type`` matches are included.
        active_provider: When ``None`` or ``"All"`` columns from ALL registered
                         providers are merged (last-wins on ``key`` collision).
                         When set to a specific provider name only that
                         provider's columns are returned.

    Returns:
        A deduplicated list of ``ColumnDef`` instances ordered by provider
        registration order, with later entries overwriting earlier ones on key
        collision.

    Notes:
        The returned ColumnDef list contains only provider-specific fields.
        Callers are responsible for prepending the base locked columns (id,
        status, timestamps, etc.) to form the full column set.
    """
    if not isinstance(schemas, dict) or not schemas:
        return []

    # Determine which providers to include
    if active_provider and active_provider != "All":
        providers_to_include = {active_provider: schemas.get(active_provider, [])}
    else:
        providers_to_include = schemas

    # Collect descriptors, deduping by key (last-wins)
    seen: dict[str, ColumnDef] = {}
    for _provider_name, descriptors in providers_to_include.items():
        if not isinstance(descriptors, list):
            continue
        for desc in descriptors:
            if not isinstance(desc, dict):
                continue
            if desc.get("resource_type") != resource_type:
                continue
            key = str(desc.get("key") or "")
            if not key:
                continue
            seen[key] = _descriptor_to_column_def(desc)

    return list(seen.values())


def resolve_provider_row_fields(
    row: dict[str, Any],
    schemas: dict[str, list[dict[str, Any]]],
    resource_type: Literal["machines", "requests", "templates"],
    active_provider: str | None,
) -> dict[str, Any]:
    """Extract provider-declared field values from a raw API row dict.

    Walks each descriptor's ``path`` (dot-separated) to extract the value
    from the row, then inserts it under the descriptor's ``key``.  The result
    is a flat dict of provider-field values that can be merged into the
    pre-formatted row dict before rendering.

    This function is called at row-format time (inside ``machine_rows``,
    ``card_rows``, etc.) so that the formatter closures produced by
    ``_make_formatter`` can do a simple ``row[key]`` lookup.

    Args:
        row:             Raw API response dict for a single resource item.
        schemas:         The full ``provider_schemas`` dict.
        resource_type:   Filter descriptor set to this resource type.
        active_provider: Provider filter (``None`` / ``"All"`` = all providers).

    Returns:
        A dict mapping descriptor key → extracted string value (empty string
        when the path resolves to None or a missing segment).
    """
    if not isinstance(schemas, dict) or not schemas:
        return {}

    if active_provider and active_provider != "All":
        providers_to_include = {active_provider: schemas.get(active_provider, [])}
    else:
        providers_to_include = schemas

    result: dict[str, Any] = {}
    for _provider_name, descriptors in providers_to_include.items():
        if not isinstance(descriptors, list):
            continue
        for desc in descriptors:
            if not isinstance(desc, dict):
                continue
            if desc.get("resource_type") != resource_type:
                continue
            key = str(desc.get("key") or "")
            path = str(desc.get("path") or key)
            if not key:
                continue
            raw_val = _dotted_get(row, path)
            result[key] = str(raw_val) if raw_val not in (None, "") else ""

    return result
