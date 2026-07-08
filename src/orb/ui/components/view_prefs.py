"""Helpers to construct standard localStorage-backed Vars for page view preferences.

Reflex 0.9 ``rx.LocalStorage`` API (confirmed from
``reflex/istate/storage.py``):

    class LocalStorage(str):
        def __new__(
            cls,
            object: Any = "",   # default value (str)
            encoding: ... = None,
            errors:   ... = None,
            /,
            name: str | None = None,  # localStorage key name
            sync: bool = False,       # propagate changes across tabs
        ) -> LocalStorage

``rx.LocalStorage`` is a ``str`` subclass used as a **default value** for a
state var.  Page States declare vars like::

    class MyState(rx.State):
        view_mode: str = rx.LocalStorage("list", name="orb-templates-view-mode")
        visible_cols: str = rx.LocalStorage(
            "name,status,created_at",
            name="orb-templates-visible-cols",
        )
        sort_key: str = rx.LocalStorage("", name="orb-templates-sort-key")
        sort_dir: str = rx.LocalStorage("asc", name="orb-templates-sort-dir")

Reflex automatically persists the var to and restores it from ``localStorage``
under the given ``name`` key.

``visible_columns`` encoding
-----------------------------
Column visibility is stored as a comma-separated string of column keys, e.g.
``"id,status,template_id,created_at"``.  The ``list_grid_view`` component uses
``visible_columns.contains(col_key)`` which maps to a JS ``String.includes()``
call.  To avoid false positives (e.g. ``"id"`` matching inside ``"request_id"``),
keys should either:
- be designed not to be substrings of each other, OR
- use the ``","+key+","`` prefix/suffix encoding that page agents opt into.

The helpers here use the plain comma-join (no wrapping commas) because the
existing column key sets in this codebase do not have ambiguous substring
relationships.  If a page's column set does, the page agent should wrap with
``","+",".join(defaults)+","`` and adjust ``on_toggle`` accordingly.

Usage
-----
    from ..components.view_prefs import view_mode_var, visible_columns_var, sort_state_vars

    class MyState(rx.State):
        view_mode: str = view_mode_var("templates")
        visible_cols: str = visible_columns_var("templates", ["name", "status", "created_at"])
        sort_key: str
        sort_dir: str
        _sk_default, _sd_default = sort_state_vars("templates", "name", "asc")
        # OR declare them directly:
        # sort_key: str = rx.LocalStorage("name", name="orb-templates-sort-key")
        # sort_dir: str = rx.LocalStorage("asc",  name="orb-templates-sort-dir")
"""

from __future__ import annotations

import reflex as rx


def view_mode_var(page: str, default: str = "list") -> rx.LocalStorage:
    """Return a LocalStorage default for the view mode of *page*.

    Args:
        page:    Short page identifier used in the ``localStorage`` key,
                 e.g. ``"templates"``, ``"requests"``, ``"machines"``.
        default: Initial value when no ``localStorage`` entry exists.
                 Must be ``"list"`` or ``"grid"``.

    Returns:
        An ``rx.LocalStorage`` instance (str subclass) suitable as a
        default value for a ``str`` state var.

    Example::

        class MyState(rx.State):
            view_mode: str = view_mode_var("templates")
    """
    return rx.LocalStorage(default, name=f"orb-{page}-view-mode")


def visible_columns_var(page: str, default: list[str]) -> rx.LocalStorage:
    """Return a LocalStorage default for the visible-columns set of *page*.

    The value is stored as a comma-separated string of column keys,
    e.g. ``"id,status,created_at"``.

    Args:
        page:    Short page identifier, e.g. ``"templates"``.
        default: List of column keys visible by default.  Typically the
                 set of ``ColumnDef`` instances where ``default_visible=True``.

    Returns:
        An ``rx.LocalStorage`` instance (str subclass).

    On the State side the var is declared as::

        class MyState(rx.State):
            visible_cols: str = visible_columns_var(
                "templates",
                ["name", "status", "created_at"],
            )

    The ``list_grid_view`` component consumes it as a Var and calls
    ``.contains(col_key)`` to determine visibility at runtime.

    To toggle a column key on/off the ``on_toggle`` handler should do::

        @rx.event
        def toggle_column(self, key: str, checked: bool) -> None:
            keys = [k for k in self.visible_cols.split(",") if k]
            if checked and key not in keys:
                keys.append(key)
            elif not checked and key in keys:
                keys.remove(key)
            self.visible_cols = ",".join(keys)
    """
    return rx.LocalStorage(",".join(default), name=f"orb-{page}-visible-cols")


def sort_state_vars(
    page: str,
    default_key: str = "",
    default_dir: str = "asc",
) -> tuple[rx.LocalStorage, rx.LocalStorage]:
    """Return a (sort_key, sort_dir) pair of LocalStorage defaults for *page*.

    Args:
        page:        Short page identifier, e.g. ``"templates"``.
        default_key: Column key to sort by initially.  Pass ``""`` for
                     no default sort.
        default_dir: Initial sort direction — ``"asc"`` or ``"desc"``.

    Returns:
        A two-tuple ``(sort_key_default, sort_dir_default)`` of
        ``rx.LocalStorage`` instances.  Assign them to state vars as::

            class MyState(rx.State):
                sort_key: str = sort_state_vars("templates", "name", "asc")[0]
                sort_dir: str = sort_state_vars("templates", "name", "asc")[1]

        Or, more readably::

            _sk, _sd = sort_state_vars("templates", "name", "asc")

            class MyState(rx.State):
                sort_key: str = _sk
                sort_dir: str = _sd

    Notes:
        The consuming State's ``set_sort`` handler should toggle the
        direction when the same column is clicked again::

            @rx.event
            def set_sort(self, key: str) -> None:
                if self.sort_key == key:
                    self.sort_dir = "desc" if self.sort_dir == "asc" else "asc"
                else:
                    self.sort_key = key
                    self.sort_dir = "asc"
    """
    sk = rx.LocalStorage(default_key, name=f"orb-{page}-sort-key")
    sd = rx.LocalStorage(default_dir, name=f"orb-{page}-sort-dir")
    return sk, sd
