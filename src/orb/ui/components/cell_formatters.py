"""Shared cell-formatter factories for list-page columns.

All three list pages (machines, requests, templates) previously defined
byte-identical copies of ``_bool_badge``, ``_json_truncate``, and
``_list_count`` with ``_m`` / ``_r`` / plain suffixes.  This module
provides the canonical implementations; each page imports and uses them
directly.

Usage
-----
    from ..components.cell_formatters import bool_badge, json_truncate, list_count

    ColumnDef("is_active", "Active", formatter=bool_badge("is_active"))
    ColumnDef("tags",      "Tags",   formatter=json_truncate("tags"))
    ColumnDef("subnet_ids","Subnets",formatter=list_count("subnet_ids"))
"""

from __future__ import annotations

from typing import Any

import reflex as rx


def bool_badge(key: str):
    """Return a formatter that renders a bool field as a yes/no badge.

    Args:
        key: The row dict key whose value is the bool to render.

    Returns:
        A callable ``(row: Any) -> rx.Component`` suitable for
        ``ColumnDef.formatter``.
    """

    def _fmt(row: Any) -> rx.Component:
        return rx.cond(
            row[key],
            rx.badge("yes", variant="soft", color_scheme="green", size="1"),
            rx.badge("no", variant="soft", color_scheme="gray", size="1"),
        )

    return _fmt


def json_truncate(key: str):
    """Return a formatter that renders a dict/JSON field as truncated code.

    Renders a monospace ``rx.code`` element capped at 12 rem with
    ``text-overflow: ellipsis``.  Shows an em-dash placeholder when the
    field is empty.

    Args:
        key: The row dict key whose value is the JSON string to render.

    Returns:
        A callable ``(row: Any) -> rx.Component`` suitable for
        ``ColumnDef.formatter``.
    """

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


def list_count(key: str):
    """Return a formatter that shows a list field as its pre-formatted count string.

    The row mapper pre-serialises list fields to ``"N items"`` strings
    (empty string when the list is empty/absent).  This formatter renders
    that string or an em-dash placeholder.

    Args:
        key: The row dict key whose value is the count string to render.

    Returns:
        A callable ``(row: Any) -> rx.Component`` suitable for
        ``ColumnDef.formatter``.
    """

    def _fmt(row: Any) -> rx.Component:
        return rx.cond(
            row[key] != "",
            rx.text(row[key], size="1", color=rx.color("gray", 11)),
            rx.text("—", size="1", color=rx.color("gray", 9)),
        )

    return _fmt
