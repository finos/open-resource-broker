"""Shared shell for list pages (machines, requests, templates).

Provides a single ``list_page_shell`` helper that enforces consistent
layout structure and ensures the content column always fills the full
available width.  All three list pages previously hand-rolled the same
vstack + rx.cond ladder; this module centralises it so layout bugs can
only exist in one place.

Width contract
--------------
The outer ``rx.vstack`` always carries ``width="100%"`` so the shell
stretches to fill the content-area ``rx.box`` that ``page()`` emits.
Without this the inner ``rx.cond`` branches have no explicit width to
inherit and the table renders narrower than the surrounding background —
the bug seen on the machines and templates pages before this refactor.

The innermost data vstack (grid + load-more) also carries
``width="100%"`` and ``align="stretch"`` so the table fills the column
even when the shell's outer box has extra padding.
"""

from __future__ import annotations

import reflex as rx


def _loading_skeleton_default() -> rx.Component:
    """Fallback skeleton — 5 rows of animated placeholder bars."""
    return rx.vstack(
        *[rx.skeleton(height="3rem", width="100%", border_radius="0.375rem") for _ in range(5)],
        spacing="2",
        width="100%",
    )


def list_page_shell(
    *,
    filter_row: rx.Component,
    toolbar: rx.Component,
    grid: rx.Component,
    load_more: rx.Component,
    empty: rx.Component,
    error_banner: rx.Component,
    banners: list[rx.Component] | None = None,
    is_loading: rx.Var,
    is_empty: rx.Var,
    loading_skeleton: rx.Component | None = None,
    dialogs: list[rx.Component] | None = None,
) -> rx.Component:
    """Compose a standard list-page layout from pre-built sub-components.

    Parameters
    ----------
    filter_row:
        The page-specific filter row (pills + search + refresh_control).
    toolbar:
        The page-specific toolbar (count badge + bulk actions + view controls).
    grid:
        The ``list_grid_view(...)`` component (already composed, no wrapping).
    load_more:
        A ``rx.cond`` block that renders the load-more button when the next
        cursor is present, or ``rx.fragment()`` otherwise.
    empty:
        The empty-state component to show when the list has no rows.
    error_banner:
        A ``rx.cond`` block for the primary error callout.
    banners:
        Optional list of additional banner components (e.g. success banners
        placed between the error callout and the filter row).
    is_loading:
        ``Var[bool]`` — True while the initial page fetch is in flight and
        no rows have been loaded yet.  Controls the skeleton vs content switch.
    is_empty:
        ``Var[bool]`` — True when the filtered row count is zero.
        Controls the empty-state vs grid switch.
    loading_skeleton:
        Optional custom skeleton component.  Falls back to a 5-row default.
    dialogs:
        Optional list of dialog/drawer components mounted at page level
        (e.g. detail drawer, confirm dialogs, request modal).

    Returns
    -------
    rx.Component
        A single ``rx.vstack`` with ``width="100%"`` ready to be passed as
        the sole content child of ``page()``.
    """
    skeleton = loading_skeleton if loading_skeleton is not None else _loading_skeleton_default()
    _banners: list[rx.Component] = banners if banners is not None else []
    _dialogs: list[rx.Component] = dialogs if dialogs is not None else []

    # Inner content area: skeleton | empty | (grid + load-more)
    # The data vstack uses width="100%" + align="stretch" so the table
    # fills the content column — this is the single authoritative place
    # where that constraint lives.
    content = rx.cond(
        is_loading,
        skeleton,
        rx.cond(
            is_empty,
            empty,
            rx.vstack(
                grid,
                load_more,
                width="100%",
                spacing="0",
                align="stretch",
            ),
        ),
    )

    return rx.vstack(
        error_banner,
        *_banners,
        filter_row,
        toolbar,
        content,
        *_dialogs,
        width="100%",
        spacing="0",
    )
