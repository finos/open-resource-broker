"""Loading skeleton placeholder component.

Renders a stack of animated skeleton bars used as a loading state while
data is being fetched.  Mirrors the React PoC's LoadingSkeleton.jsx visual
language.

Usage
-----
    from ..components.loading_skeleton import loading_skeleton

    # Default — 5 rows at 2.5 rem height:
    loading_skeleton()

    # Custom — 3 rows, taller bars:
    loading_skeleton(rows=3, height="3rem")
"""

from __future__ import annotations

import reflex as rx


def loading_skeleton(
    rows: int = 5,
    height: str = "2.5rem",
    border_radius: str = "0.5rem",
) -> rx.Component:
    """Return a vertical stack of animated skeleton bars.

    Args:
        rows:          Number of skeleton bars to render.  Defaults to 5.
        height:        CSS height applied to each bar.  Defaults to ``"2.5rem"``.
        border_radius: CSS border-radius applied to each bar.
                       Defaults to ``"0.5rem"``.

    Returns:
        An ``rx.vstack`` containing *rows* ``rx.skeleton`` components, each
        spanning the full available width.

    Example::

        rx.cond(
            MyState.loading,
            loading_skeleton(rows=3),
            my_data_table(),
        )
    """
    return rx.vstack(
        *[
            rx.skeleton(height=height, border_radius=border_radius, width="100%")
            for _ in range(rows)
        ],
        spacing="2",
        width="100%",
    )
