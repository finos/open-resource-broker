"""Sortable column header cell for use in rx.table.

Usage
-----
    from ..components.sortable_header import sortable_header

    # Inside an rx.table.header row:
    sortable_header(
        title="Created",
        col_key="created_at",
        sort_key=MyState.sort_key,
        sort_dir=MyState.sort_dir,
        on_sort=MyState.set_sort,
    )

The component renders an ``rx.table.column_header_cell`` with a clickable
``rx.hstack`` that shows a chevron-up/down icon when the column is the
currently active sort column.  Inactive columns show no icon.
"""

from __future__ import annotations

import reflex as rx


def sortable_header(
    title: str,
    col_key: str,
    sort_key: rx.Var,
    sort_dir: rx.Var,
    on_sort,  # event handler accepting (key: str) — called with col_key
) -> rx.Component:
    """Clickable column header that shows a sort direction indicator.

    Args:
        title:    Human-readable column label.
        col_key:  The string key passed to ``on_sort`` and compared against
                  ``sort_key`` to determine whether this column is active.
        sort_key: A ``rx.Var[str]`` holding the currently sorted column key.
        sort_dir: A ``rx.Var[str]`` holding the current sort direction
                  (``"asc"`` or ``"desc"``).
        on_sort:  Event handler invoked with ``col_key`` when the header is
                  clicked.  The handler is responsible for toggling the
                  direction when the column is already active.

    Returns:
        An ``rx.table.column_header_cell`` that is keyboard- and
        pointer-accessible.

    Example::

        sortable_header(
            "Created",
            "created_at",
            MyState.sort_key,
            MyState.sort_dir,
            MyState.set_sort,
        )
    """
    # Chevron shown only when this column is the active sort column.
    # direction_icon is evaluated at runtime via rx.cond.
    direction_icon = rx.cond(
        sort_dir == "asc",
        rx.icon("chevron-up", size=14, aria_hidden="true"),
        rx.icon("chevron-down", size=14, aria_hidden="true"),
    )

    sort_indicator = rx.cond(
        sort_key == col_key,
        direction_icon,
        rx.fragment(),
    )

    return rx.table.column_header_cell(
        rx.hstack(
            rx.text(title, size="2", weight="medium"),
            sort_indicator,
            spacing="1",
            align="center",
            cursor="pointer",
            _hover={"color": rx.color("blue", 11)},
        ),
        on_click=on_sort(col_key),
        cursor="pointer",
        aria_sort=rx.cond(
            sort_key == col_key,
            rx.cond(sort_dir == "asc", "ascending", "descending"),
            "none",
        ),
    )
