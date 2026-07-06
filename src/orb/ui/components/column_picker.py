"""Column visibility picker popover.

Usage
-----
    from ..components.column_picker import column_picker
    from ..components.list_grid_view import ColumnDef

    COLUMNS = [
        ColumnDef("id",         "ID",       lockable=True),
        ColumnDef("status",     "Status"),
        ColumnDef("created_at", "Created"),
    ]

    # Inside a toolbar hstack:
    column_picker(
        columns=COLUMNS,
        visible_columns=MyState.visible_cols,
        on_toggle=MyState.toggle_column,
    )

The popover lists every non-lockable column with a checkbox.  Ticking or
unticking a checkbox fires ``on_toggle(col.key, checked)`` so the consuming
State can add/remove the key from its ``visible_columns`` string.

Lockable columns are omitted from the picker entirely because they cannot
be hidden.
"""

from __future__ import annotations

import reflex as rx

from .list_grid_view import ColumnDef


def column_picker(
    columns: list[ColumnDef],
    visible_columns: rx.Var,
    on_toggle,  # event handler accepting (key: str, checked: bool)
) -> rx.Component:
    """A popover-triggered column visibility picker.

    Args:
        columns:         Python-level list of ``ColumnDef``.  Iterated at
                         compile time.  Non-lockable columns appear as
                         toggleable checkboxes.
        visible_columns: ``rx.Var[str]`` — comma-separated visible column
                         keys, produced by ``view_prefs.visible_columns_var``.
                         Used to drive each checkbox's ``checked`` state via
                         the Var ``contains`` method.
        on_toggle:       Event handler called as ``on_toggle(col.key, checked)``
                         when a checkbox changes.  The handler should add or
                         remove the key from the ``visible_columns`` string on
                         the State side.

    Returns:
        An ``rx.popover.root`` wrapping a "Columns" button trigger and a
        content panel listing checkboxes for every non-lockable column.

    Notes:
        The popover is modal-like on mobile (full-screen on small viewports
        via Radix default behaviour) and a floating panel on desktop.

    Example::

        column_picker(
            columns=MY_COLUMNS,
            visible_columns=MyState.visible_cols,
            on_toggle=MyState.toggle_column,
        )
    """
    # Only show non-lockable columns in the picker.
    toggleable = [col for col in columns if not col.lockable]

    # Build the list of checkboxes at compile time.
    # Use fenced search (",key,") to avoid substring false-positives.
    # e.g. "name" must not match "key_name" or "provider_name".
    # visible_columns is stored as ",key1,key2,...,key_n," (leading + trailing comma).
    checkbox_items = [
        rx.hstack(
            rx.checkbox(
                checked=visible_columns.contains("," + col.key + ","),  # type: ignore[attr-defined]
                on_change=on_toggle(col.key),
                id=f"col-toggle-{col.key}",
            ),
            rx.text(col.title, size="2", as_="label", html_for=f"col-toggle-{col.key}"),
            spacing="2",
            align="center",
            width="100%",
        )
        for col in toggleable
    ]

    return rx.popover.root(
        rx.popover.trigger(
            rx.tooltip(
                rx.icon_button(
                    rx.icon("columns-3", size=16, aria_hidden="true"),
                    variant="ghost",
                    size="2",
                    color_scheme="gray",
                    aria_label="Toggle columns",
                ),
                content="Toggle columns",
            ),
        ),
        rx.popover.content(
            rx.vstack(
                rx.text(
                    "Toggle columns",
                    size="2",
                    weight="medium",
                    color=rx.color("gray", 11),
                    margin_bottom="0.25rem",
                ),
                rx.divider(),
                *checkbox_items,
                spacing="2",
                align="start",
                padding="0.5rem 0",
                min_width="160px",
            ),
            side="bottom",
            align="end",
        ),
    )
