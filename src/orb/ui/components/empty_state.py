"""Empty-state placeholder component.

Used in tables and lists when there is no data to display — either because
nothing exists yet or because the current filters produce zero results.

Matches the React PoC's EmptyState.jsx visual language:
  - Large icon in muted gray
  - Bold heading
  - Smaller description text
  - Optional action button (e.g. "Refresh" or "Clear filters")

Usage
-----
    from ..components.empty_state import empty_state

    # Basic — no action:
    empty_state(
        icon="server-off",
        title="No machines found",
        description="No machines have been allocated yet.",
    )

    # With an action button:
    empty_state(
        icon="inbox",
        title="No requests",
        description="Try adjusting your filters.",
        action=rx.button("Clear filters", on_click=MyState.clear_filters),
    )
"""

from __future__ import annotations

from typing import Optional

import reflex as rx


def empty_state(
    icon: str,
    title: str,
    description: str,
    action: Optional[rx.Component] = None,
) -> rx.Component:
    """Render a centered empty-state placeholder.

    Args:
        icon:        Lucide icon name (e.g. ``"server-off"``, ``"inbox"``).
        title:       Short heading shown below the icon.
        description: One-sentence explanation or hint to the user.
        action:      Optional component (usually a button) rendered below the
                     description.  Pass ``None`` to omit.

    Returns:
        A centered ``rx.vstack`` suitable for use inside a table body or a
        full-width content area.

    Example::

        empty_state(
            icon="server-off",
            title="No machines found",
            description="Try adjusting your filters.",
            action=rx.button(
                "Refresh",
                on_click=MachinesState.load,
                variant="soft",
            ),
        )
    """
    children: list[rx.Component] = [
        # aria_hidden: the icon is purely decorative — the heading and
        # description already convey the empty-state meaning to assistive tech.
        rx.icon(
            icon,
            size=48,
            color=rx.color("gray", 8),
            aria_hidden="true",
        ),
        rx.heading(
            title,
            size="4",
            color=rx.color("gray", 11),
        ),
        rx.text(
            description,
            size="2",
            color=rx.color("gray", 10),
            text_align="center",
            max_width="24rem",
        ),
    ]

    if action is not None:
        children.append(action)

    # role="status" surfaces the empty-state message to screen readers via the
    # live region; aria-live="polite" avoids interrupting ongoing announcements.
    return rx.vstack(
        *children,
        spacing="3",
        align="center",
        padding="4rem 2rem",
        width="100%",
        role="status",
        aria_live="polite",
    )
