"""Segmented list / grid view-mode toggle.

Usage
-----
    from ..components.view_toggle import view_toggle

    # Inside a toolbar hstack (mode is an rx.Var[str]):
    view_toggle(mode=MyState.view_mode, on_change=MyState.set_view_mode)

The toggle renders two ``rx.icon_button`` components side-by-side.  The
button matching the current ``mode`` value uses the ``"solid"`` variant
(visually active); the other uses ``"ghost"``.
"""

from __future__ import annotations

import reflex as rx


def view_toggle(
    mode: rx.Var,
    on_change,  # event handler accepting a str ("list" | "grid")
) -> rx.Component:
    """Segmented list/grid toggle control.

    Args:
        mode:      A ``rx.Var[str]`` with value ``"list"`` or ``"grid"``.
        on_change: An event handler that receives the new mode string when
                   the user clicks a button.  Typically a single-arg state
                   event such as ``MyState.set_view_mode``.

    Returns:
        A compact ``rx.hstack`` containing two icon buttons.  The active
        button is rendered with the ``"solid"`` variant; the inactive one
        with ``"ghost"`` so the selection is obvious at a glance.

    Example::

        view_toggle(
            mode=TemplatesState.view_mode,
            on_change=TemplatesState.set_view_mode,
        )
    """
    list_variant = rx.cond(mode == "list", "solid", "ghost")
    grid_variant = rx.cond(mode == "grid", "solid", "ghost")

    return rx.hstack(
        rx.tooltip(
            rx.icon_button(
                rx.icon("list", size=16, aria_hidden="true"),
                variant=list_variant,  # type: ignore[arg-type]
                size="2",
                color_scheme="gray",
                on_click=on_change("list"),
                aria_label="Switch to list view",
                aria_pressed=rx.cond(mode == "list", "true", "false"),
            ),
            content="List view",
        ),
        rx.tooltip(
            rx.icon_button(
                rx.icon("layout-grid", size=16, aria_hidden="true"),
                variant=grid_variant,  # type: ignore[arg-type]
                size="2",
                color_scheme="gray",
                on_click=on_change("grid"),
                aria_label="Switch to grid view",
                aria_pressed=rx.cond(mode == "grid", "true", "false"),
            ),
            content="Grid view",
        ),
        spacing="1",
        align="center",
        role="group",
        aria_label="View mode",
    )
