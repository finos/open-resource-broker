"""Shared drawer section helper.

Provides a single ``drawer_section()`` factory used by machine_drawer.py,
request_drawer.py, and template_drawer.py so the three previously
incompatible ``_section()`` private functions stay in sync.

Usage::

    from .drawer_utils import drawer_section

    # Default (no box) — matches machine_drawer / template_drawer style:
    drawer_section("Identity", field_row(...), field_row(...))

    # Boxed — matches request_drawer style:
    drawer_section("Progress", progress_bar(...), boxed=True)
"""

from __future__ import annotations

import reflex as rx


def drawer_section(title: str, *content: rx.Component, boxed: bool = False) -> rx.Component:
    """A titled group of content rows inside a drawer panel.

    Args:
        title: Section heading text.
        *content: Child components to render below the title.
        boxed: When ``True`` render a bordered card (request_drawer style:
               gray-2 background, border, padding, title in size-1 / gray-11).
               When ``False`` (default) render a bare vstack with a divider
               (machine_drawer / template_drawer style: title in size-3 /
               gray-12, spacing="2", padding_bottom="1rem").

    Returns:
        A Reflex component suitable for use inside a drawer body.
    """
    if boxed:
        return rx.box(
            rx.text(title, size="1", weight="bold", color=rx.color("gray", 11), mb="2"),
            *content,
            padding="0.75rem 1rem",
            background=rx.color("gray", 2),
            border_radius="0.5rem",
            border=f"1px solid {rx.color('gray', 5)}",
            width="100%",
        )
    return rx.vstack(
        rx.text(title, size="3", weight="bold", color=rx.color("gray", 12)),
        rx.divider(),
        *content,
        spacing="2",
        align="start",
        width="100%",
        padding_bottom="1rem",
    )
