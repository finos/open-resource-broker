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

Load-more
---------
Callers may either:

1. Pass ``next_cursor``, ``loading_more``, and ``on_load_more`` as
   primitives — the shell builds the canonical button internally.
2. Pass a pre-built ``load_more`` component for full customisation.

Option 1 is preferred for all three list pages; option 2 is kept for
backward compatibility and custom page layouts.
"""

from __future__ import annotations

from typing import Any, Optional

import reflex as rx


def _loading_skeleton_default() -> rx.Component:
    """Fallback skeleton — 5 rows of animated placeholder bars."""
    return rx.vstack(
        *[rx.skeleton(height="3rem", width="100%", border_radius="0.375rem") for _ in range(5)],
        spacing="2",
        width="100%",
    )


def _build_load_more_button(
    next_cursor: rx.Var,
    loading_more: rx.Var,
    on_load_more: Any,
) -> rx.Component:
    """Build the canonical load-more button from state primitives.

    Parameters
    ----------
    next_cursor:
        ``Var[str]`` — non-empty when a next page exists.
    loading_more:
        ``Var[bool]`` — True while the page-append fetch is in flight.
    on_load_more:
        Event handler called when the button is clicked.

    Returns
    -------
    rx.Component
        A ``rx.cond`` that renders the button when ``next_cursor`` is
        non-empty, or ``rx.fragment()`` otherwise.
    """
    return rx.cond(
        next_cursor != "",
        rx.center(
            rx.button(
                rx.cond(
                    loading_more,
                    rx.spinner(size="2"),
                    rx.icon("chevrons-down", size=16),
                ),
                rx.cond(
                    loading_more,
                    "Loading…",
                    "Load more",
                ),
                on_click=on_load_more,
                disabled=loading_more,
                variant="soft",
                color_scheme="gray",
                size="2",
            ),
            width="100%",
            padding_top="0.75rem",
        ),
        rx.fragment(),
    )


def list_page_shell(
    *,
    filter_row: rx.Component,
    toolbar: rx.Component,
    grid: rx.Component,
    empty: rx.Component,
    error_banner: rx.Component,
    is_loading: rx.Var,
    is_empty: rx.Var,
    # Load-more: either pass primitives (preferred) or a pre-built component
    load_more: Optional[rx.Component] = None,
    next_cursor: Optional[rx.Var] = None,
    loading_more: Optional[rx.Var] = None,
    on_load_more: Optional[Any] = None,
    banners: list[rx.Component] | None = None,
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
    empty:
        The empty-state component to show when the list has no rows.
    error_banner:
        A ``rx.cond`` block for the primary error callout.
    is_loading:
        ``Var[bool]`` — True while the initial page fetch is in flight and
        no rows have been loaded yet.  Controls the skeleton vs content switch.
    is_empty:
        ``Var[bool]`` — True when the filtered row count is zero.
        Controls the empty-state vs grid switch.
    load_more:
        Optional pre-built load-more component.  Takes precedence over the
        ``next_cursor`` / ``loading_more`` / ``on_load_more`` primitives.
        Falls back to ``rx.fragment()`` when all four are omitted.
    next_cursor:
        ``Var[str]`` — non-empty when a next page is available.  Used to
        build the internal load-more button when ``load_more`` is not given.
    loading_more:
        ``Var[bool]`` — True while the page-append fetch is in flight.
    on_load_more:
        Event handler for the load-more button click.
    banners:
        Optional list of additional banner components (e.g. success banners
        placed between the error callout and the filter row).
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

    # Resolve the load-more component.
    # Priority: explicit load_more > primitives > empty fragment
    if load_more is not None:
        _load_more = load_more
    elif next_cursor is not None and loading_more is not None and on_load_more is not None:
        _load_more = _build_load_more_button(next_cursor, loading_more, on_load_more)
    else:
        _load_more = rx.fragment()

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
                _load_more,
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
