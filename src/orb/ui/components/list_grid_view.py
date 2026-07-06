"""Generic list/grid view component with column visibility, sorting, and view-mode switching.

This module provides:

- ``ColumnDef``  — compile-time column descriptor dataclass
- ``list_grid_view`` — the main switchable list/grid component

Reflex 0.9 constraints
----------------------

**Column visibility at runtime:**
``visible_columns`` is a ``rx.Var[str]`` (comma-joined keys, backed by
``rx.LocalStorage``).  You CANNOT iterate a Var at compile time.  The
pattern used here is:

1.  At compile time (Python-level) iterate ``columns`` to emit one cell
    per column.
2.  Wrap every cell in ``rx.cond(visible_columns.contains(col.key), cell,
    rx.fragment())`` so columns are shown or hidden at runtime without
    triggering a re-compile.

**Sort is server-side:**
The consuming page's State owns ``sort_key: str``, ``sort_dir: str``, and a
``sorted_rows`` computed var.  The ``on_sort`` handler receives the column
key; it flips the direction when the same key is clicked twice.

Usage
-----
    from ..components.list_grid_view import ColumnDef, list_grid_view

    COLUMNS = [
        ColumnDef("request_id", "Request ID", lockable=True, sortable=True),
        ColumnDef("status",     "Status",     formatter=lambda row: request_status_badge(row["status"])),
        ColumnDef("template_id","Template",   sortable=True, default_visible=True),
        ColumnDef("created_at", "Created",    sortable=True),
    ]

    # In the page component:
    list_grid_view(
        rows=RequestsState.sorted_rows,
        columns=COLUMNS,
        view_mode=RequestsState.view_mode,
        visible_columns=RequestsState.visible_columns_var,
        sort_key=RequestsState.sort_key,
        sort_dir=RequestsState.sort_dir,
        card_renderer=_request_card,
        on_row_click=RequestsState.open_drawer,
        on_sort=RequestsState.set_sort,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import reflex as rx

from .sortable_header import sortable_header

# ---------------------------------------------------------------------------
# ColumnDef
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnDef:
    """Compile-time column descriptor for ``list_grid_view``.

    Attributes:
        key:             The dict key used to look up the value in each row
                         dict (e.g. ``"status"``, ``"created_at"``).
        title:           Human-readable column heading.
        formatter:       Optional callable ``(row: Var) -> rx.Component``
                         that renders the cell.  When ``None`` the cell
                         renders ``rx.text(row[key])``.
        default_visible: Whether the column is shown by default.  Pages
                         initialise their ``visible_columns`` LocalStorage
                         from the set of keys where this is ``True``.
        lockable:        When ``True`` the column is always shown and hidden
                         from the column picker (cannot be toggled off).
        sortable:        When ``True`` the column header is rendered as a
                         ``sortable_header`` button.
        width:           Optional CSS width string (e.g. ``"120px"``).
        align:           Cell alignment: ``"start"`` | ``"center"`` | ``"end"``.
    """

    key: str
    title: str
    formatter: Optional[Callable[[Any], rx.Component]] = field(
        default=None, hash=False, compare=False
    )
    default_visible: bool = True
    lockable: bool = False
    sortable: bool = False
    width: Optional[str] = None
    align: str = "start"
    # Optional custom header renderer — receives no args, returns the
    # contents of the column_header_cell. Used for the "_select" column to
    # render a tri-state "select all visible" checkbox instead of the
    # static title string.
    header_renderer: Optional[Callable[[], rx.Component]] = field(
        default=None, hash=False, compare=False
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cell_content(col: ColumnDef, row: rx.Var) -> rx.Component:
    """Render the cell content for *col* from the *row* Var.

    If the column has a custom formatter it is called with *row*.
    Otherwise we fall back to ``rx.text(row[col.key])``.
    """
    if col.formatter is not None:
        return col.formatter(row)
    return rx.text(row[col.key], size="2")  # type: ignore[index]


def _header_cell(
    col: ColumnDef,
    sort_key: rx.Var,
    sort_dir: rx.Var,
    on_sort,
) -> rx.Component:
    """Build a header cell (custom / sortable / plain)."""
    # Custom header renderer takes priority — used by selection columns to
    # render a "select all visible" checkbox in the header.
    if col.header_renderer is not None:
        header_kwargs: dict[str, Any] = {}
        if col.width:
            header_kwargs["width"] = col.width
        return rx.table.column_header_cell(col.header_renderer(), **header_kwargs)
    if col.sortable and on_sort is not None:
        inner = sortable_header(
            title=col.title,
            col_key=col.key,
            sort_key=sort_key,
            sort_dir=sort_dir,
            on_sort=on_sort,
        )
        # sortable_header already returns an rx.table.column_header_cell,
        # so we return it directly, but we want to control width here.
        # To keep it simple we wrap the cell's content only via sortable_header.
        if col.width:
            return rx.table.column_header_cell(
                rx.hstack(
                    rx.text(col.title, size="2", weight="medium"),
                    rx.cond(
                        sort_key == col.key,
                        rx.cond(
                            sort_dir == "asc",
                            rx.icon("chevron-up", size=14, aria_hidden="true"),
                            rx.icon("chevron-down", size=14, aria_hidden="true"),
                        ),
                        rx.fragment(),
                    ),
                    spacing="1",
                    align="center",
                    cursor="pointer",
                    _hover={"color": rx.color("blue", 11)},
                ),
                on_click=on_sort(col.key),
                cursor="pointer",
                width=col.width,
            )
        return inner
    header_kwargs: dict[str, Any] = {}
    if col.width:
        header_kwargs["width"] = col.width
    return rx.table.column_header_cell(col.title, **header_kwargs)


def _data_cell(col: ColumnDef, row: rx.Var) -> rx.Component:
    """Build a table data cell for *col*."""
    cell_kwargs: dict[str, Any] = {"vertical_align": "middle"}
    if col.width:
        cell_kwargs["width"] = col.width
    return rx.table.cell(
        _cell_content(col, row),
        **cell_kwargs,
    )


# ---------------------------------------------------------------------------
# _list_view
# ---------------------------------------------------------------------------


def _list_view(
    rows: rx.Var,
    columns: list[ColumnDef],
    visible_columns: rx.Var,
    sort_key: rx.Var,
    sort_dir: rx.Var,
    on_row_click,
    on_sort,
) -> rx.Component:
    """Render the table (list) view.

    Every column is emitted at compile time.  Runtime visibility is
    controlled by ``rx.cond(visible_columns.contains(col.key), cell,
    rx.fragment())``.  Lockable columns skip the ``rx.cond`` and are
    always rendered.
    """

    def _make_row(row: rx.Var) -> rx.Component:
        cells = []
        for col in columns:
            cell = _data_cell(col, row)
            if col.lockable:
                cells.append(cell)
            else:
                # Fenced search: ",key," avoids substring false-positives.
                # e.g. "name" must not match "key_name" or "provider_name".
                # visible_columns is stored as ",key1,key2,...,key_n,".
                cells.append(
                    rx.cond(
                        visible_columns.contains("," + col.key + ","),  # type: ignore[attr-defined]
                        cell,
                        rx.fragment(),
                    )
                )
        row_kwargs: dict[str, Any] = {
            "_hover": {"background": rx.color("gray", 2)},
        }
        if on_row_click is not None:
            row_kwargs["on_click"] = on_row_click(row)
            row_kwargs["cursor"] = "pointer"
        return rx.table.row(*cells, **row_kwargs)

    # Build header cells compile-time
    header_cells = []
    for col in columns:
        hcell = _header_cell(col, sort_key, sort_dir, on_sort)
        if col.lockable:
            header_cells.append(hcell)
        else:
            # Fenced search: ",key," avoids substring false-positives.
            header_cells.append(
                rx.cond(
                    visible_columns.contains("," + col.key + ","),  # type: ignore[attr-defined]
                    hcell,
                    rx.fragment(),
                )
            )

    # Wrap the table in a horizontally-scrollable box so narrow viewports
    # don't blow out the layout when many columns are visible.
    return rx.box(
        rx.table.root(
            rx.table.header(
                rx.table.row(*header_cells),
            ),
            rx.table.body(
                rx.foreach(rows, _make_row),
            ),
            variant="surface",
            width="100%",
            style={"minWidth": "max-content"},
        ),
        width="100%",
        overflow_x="auto",
    )


# ---------------------------------------------------------------------------
# _grid_view
# ---------------------------------------------------------------------------


def _grid_view(
    rows: rx.Var,
    card_renderer: Callable[[Any], rx.Component],
) -> rx.Component:
    """Render the card grid view using *card_renderer* per row."""
    return rx.grid(
        rx.foreach(rows, card_renderer),
        columns="repeat(auto-fill, minmax(280px, 1fr))",
        gap="1rem",
        width="100%",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_grid_view(
    rows: rx.Var,
    columns: list[ColumnDef],
    view_mode: rx.Var,
    visible_columns: rx.Var,
    sort_key: rx.Var,
    sort_dir: rx.Var,
    card_renderer: Callable[[Any], rx.Component],
    on_row_click=None,
    on_sort=None,
) -> rx.Component:
    """Switchable list/grid view component.

    Renders either a sortable table (``"list"`` mode) or a responsive card
    grid (``"grid"`` mode) depending on ``view_mode``.

    Args:
        rows:           ``rx.Var`` containing the list of pre-formatted row
                        dicts.  Typically a computed var on the page State
                        that returns already-sorted rows.
        columns:        Python-level list of ``ColumnDef`` descriptors,
                        iterated at compile time to emit static column
                        definitions.  Do NOT pass a Var here.
        view_mode:      ``rx.Var[str]`` — ``"list"`` or ``"grid"``.
        visible_columns: ``rx.Var[str]`` — comma-separated list of visible
                        column keys (e.g. ``"id,status,created_at"``).  The
                        ``contains`` Var method is used to check membership
                        at runtime.  Lockable columns ignore this.
        sort_key:       ``rx.Var[str]`` — the currently sorted column key.
        sort_dir:       ``rx.Var[str]`` — ``"asc"`` or ``"desc"``.
        card_renderer:  A Python callable ``(row: Var) -> rx.Component``
                        used to render each card in grid mode.
        on_row_click:   Optional event handler ``(row: Var)`` invoked when
                        a table row is clicked in list mode.
        on_sort:        Optional event handler ``(col_key: str)`` invoked
                        when a sortable column header is clicked.

    Returns:
        An ``rx.cond`` that switches between the list and grid views at
        runtime.

    Notes:
        - ``visible_columns`` must be stored as a **fenced** comma-separated
          string: ``",key1,key2,...,keyN,"`` (leading **and** trailing comma).
          The ``contains`` check uses ``",key,"`` substring matching to avoid
          false positives where one column key is a prefix of another (e.g.
          ``"name"`` vs ``"key_name"`` or ``"provider_name"``).
          Use ``_fenced_cols(keys)`` / ``_unfenced_cols(s)`` helpers in your
          State to encode/decode.
        - Sort happens server-side; this component only fires ``on_sort``
          events.  The consuming State is responsible for producing
          ``sorted_rows``.

    Example::

        list_grid_view(
            rows=MyState.sorted_rows,
            columns=MY_COLUMNS,
            view_mode=MyState.view_mode,
            visible_columns=MyState.visible_cols,
            sort_key=MyState.sort_key,
            sort_dir=MyState.sort_dir,
            card_renderer=_my_card,
            on_row_click=MyState.open_drawer,
            on_sort=MyState.set_sort,
        )
    """
    list_view = _list_view(
        rows=rows,
        columns=columns,
        visible_columns=visible_columns,
        sort_key=sort_key,
        sort_dir=sort_dir,
        on_row_click=on_row_click,
        on_sort=on_sort,
    )

    grid_view = _grid_view(rows=rows, card_renderer=card_renderer)

    # Mobile: list view is unusable at narrow widths even with overflow_x
    # scrolling — too much horizontal hunting for IDs and statuses. Force
    # the card grid on small viewports regardless of the saved view_mode
    # preference. The user's desktop preference is preserved in
    # localStorage and reapplied when the viewport widens.
    return rx.fragment(
        rx.mobile_only(grid_view),
        rx.tablet_and_desktop(
            rx.cond(view_mode == "list", list_view, grid_view),
        ),
    )
