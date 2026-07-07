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

Provider grouping
-----------------
Pass ``provider_column_keys`` (a set of column keys that come from provider
schemas) to render base columns without a header and provider columns under a
labelled group heading.  When ``provider_column_keys`` is empty or omitted all
columns are rendered in a flat list (original behaviour).
"""

from __future__ import annotations

import reflex as rx

from .list_grid_view import ColumnDef


def column_picker(
    columns: list[ColumnDef],
    visible_columns: rx.Var,
    on_toggle,  # event handler accepting (key: str, checked: bool)
    provider_column_keys: set[str] | None = None,
) -> rx.Component:
    """A popover-triggered column visibility picker.

    Args:
        columns:              Python-level list of ``ColumnDef``.  Iterated at
                              compile time.  Non-lockable columns appear as
                              toggleable checkboxes.
        visible_columns:      ``rx.Var[str]`` — comma-separated visible column
                              keys, produced by ``view_prefs.visible_columns_var``.
                              Used to drive each checkbox's ``checked`` state via
                              the Var ``contains`` method.
        on_toggle:            Event handler called as ``on_toggle(col.key, checked)``
                              when a checkbox changes.  The handler should add or
                              remove the key from the ``visible_columns`` string on
                              the State side.
        provider_column_keys: Optional set of column keys that originate from
                              provider schemas.  When provided, base columns are
                              listed first (no group header) and provider columns
                              are rendered under a ``[Provider]`` group label.
                              When ``None`` or empty all columns appear in a flat
                              list (original behaviour, backwards-compatible).

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
            provider_column_keys={"aws_instance_type", "aws_price_type"},
        )
    """
    # Only show non-lockable columns in the picker.
    toggleable = [col for col in columns if not col.lockable]

    def _checkbox(col: ColumnDef) -> rx.Component:
        return rx.hstack(
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

    if provider_column_keys:
        base_cols = [col for col in toggleable if col.key not in provider_column_keys]
        prov_cols = [col for col in toggleable if col.key in provider_column_keys]

        body_items: list[rx.Component] = [_checkbox(col) for col in base_cols]

        if prov_cols:
            # Infer provider label from first matching column's title prefix or
            # fall back to a generic "Provider" heading.
            first_key = prov_cols[0].key
            provider_label = first_key.split("_")[0].upper() if "_" in first_key else "Provider"
            body_items.append(
                rx.hstack(
                    rx.text(
                        f"[{provider_label}]",
                        size="1",
                        weight="bold",
                        color=rx.color("blue", 10),
                        margin_top="0.5rem",
                    ),
                    rx.divider(orientation="horizontal", flex="1"),
                    spacing="2",
                    align="center",
                    width="100%",
                )
            )
            body_items.extend(_checkbox(col) for col in prov_cols)
    else:
        body_items = [_checkbox(col) for col in toggleable]

    return rx.popover.root(
        rx.popover.trigger(
            # NOTE: ``rx.tooltip`` inserts an extra wrapper element that
            # breaks Radix Popover's ``asChild`` slot semantics — the
            # trigger button then never registers a click on the
            # underlying popover state, so the popover appears not to
            # open at all.  Keep the icon-button as the direct child of
            # the trigger; the ``title`` prop provides an accessible
            # hover-hint without the extra wrapper.
            rx.icon_button(
                rx.icon("columns-3", size=16, aria_hidden="true"),
                variant="ghost",
                size="2",
                color_scheme="gray",
                aria_label="Toggle columns",
                title="Toggle columns",
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
                *body_items,
                spacing="2",
                align="start",
                padding="0.5rem 0",
                min_width="160px",
            ),
            side="bottom",
            align="end",
        ),
    )
