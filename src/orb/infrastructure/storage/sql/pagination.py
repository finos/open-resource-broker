"""Keyset page queries for the typed SQL tables.

The database must order rows exactly as the in-process pager does
(``orb.domain.base.pagination``): by the sort text and then the id, compared
byte-wise, with a missing sort value as the empty string. Each dialect needs its
own spelling of "byte-wise":

* SQLite compares text byte-wise by default.
* PostgreSQL uses the database locale unless told ``COLLATE "C"``.
* MySQL and Aurora MySQL default to case- and accent-insensitive collations, so
  both the ordering and the equality in the keyset predicate would treat ``a``
  and ``A`` as the same value; ``CAST(... AS BINARY)`` compares bytes.

Any other dialect gets no push-down: the caller pages in process instead.

Only allow-listed sort and filter columns are used; nothing from the request is
ever interpolated into SQL.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import (
    Column,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    and_,
    bindparam,
    cast,
    collate,
    func,
    or_,
    select,
)
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

SORT_KEY_LABEL = "_orb_sort_key"

# Columns a list may be ordered by in the database, per table. Each is text or
# is normalised to text by COALESCE; their stored text sorts like the in-process
# key. ``launch_time`` is declared BIGINT but holds ISO text, which only SQLite
# accepts, so it is offered on SQLite alone.
_SORTS: dict[str, frozenset[str]] = {
    "requests": frozenset({"created_at", "status", "request_id"}),
    "machines": frozenset({"created_at", "status", "machine_id"}),
}
_SQLITE_ONLY_SORTS: dict[str, frozenset[str]] = {"machines": frozenset({"launch_time"})}

# Equality filters the database may apply, per table
_FILTERS: dict[str, frozenset[str]] = {
    "requests": frozenset(
        {"status", "provider_type", "provider_name", "template_id", "request_type"}
    ),
    "machines": frozenset({"status", "provider_type", "request_id"}),
}

_BINARY_DIALECTS = {"sqlite", "postgresql", "mysql", "mariadb"}


def supports_sort(table: str, sort_column: str, dialect: str) -> bool:
    """Whether rows of ``table`` can be ordered by ``sort_column`` in the database."""
    if dialect not in _BINARY_DIALECTS:
        return False
    if sort_column in _SORTS.get(table, frozenset()):
        return True
    return dialect == "sqlite" and sort_column in _SQLITE_ONLY_SORTS.get(table, frozenset())


def supports_filters(table: str, criteria: dict[str, Any]) -> bool:
    """Whether every filter in ``criteria`` is an allow-listed equality on a string."""
    allowed = _FILTERS.get(table, frozenset())
    return all(name in allowed and isinstance(value, str) for name, value in criteria.items())


def binary_ordered(expression: ColumnElement[Any], dialect: str) -> ColumnElement[Any]:
    """``expression`` compared and ordered byte-wise on ``dialect``."""
    if dialect == "postgresql":
        return collate(expression, "C")
    if dialect in ("mysql", "mariadb"):
        return cast(expression, LargeBinary)
    return expression


def _table(name: str, columns: list[str]) -> Table:
    return Table(name, MetaData(), *(Column(column, Text) for column in columns))


def build_page_statement(
    *,
    table: str,
    columns: list[str],
    id_column: str,
    sort_column: str,
    descending: bool,
    criteria: dict[str, Any],
    after: Optional[tuple[str, str]],
    limit: int,
    dialect: str,
) -> Optional[Select[Any]]:
    """The query for one page (``limit + 1`` rows), or None when it cannot be pushed down."""
    if not supports_sort(table, sort_column, dialect) or not supports_filters(table, criteria):
        return None
    if sort_column not in columns or id_column not in columns:
        return None
    if any(name not in columns for name in criteria):
        return None

    source = _table(table, columns)
    sort_text = func.coalesce(source.c[sort_column], "")
    sort_key = binary_ordered(sort_text, dialect)
    id_key = binary_ordered(source.c[id_column], dialect)

    statement = select(*source.c, sort_text.label(SORT_KEY_LABEL))
    for name, value in criteria.items():
        # Byte-wise like every other backend: MySQL's default collations would
        # match "AWS-a" with "aws-a" and ignore trailing spaces
        statement = statement.where(binary_ordered(source.c[name], dialect) == value)

    if after is not None:
        value_param = bindparam("after_value", after[0], type_=String)
        id_param = bindparam("after_id", after[1], type_=String)
        if descending:
            statement = statement.where(
                or_(sort_key < value_param, and_(sort_key == value_param, id_key < id_param))
            )
        else:
            statement = statement.where(
                or_(sort_key > value_param, and_(sort_key == value_param, id_key > id_param))
            )

    if descending:
        statement = statement.order_by(sort_key.desc(), id_key.desc())
    else:
        statement = statement.order_by(sort_key.asc(), id_key.asc())
    return statement.limit(limit + 1)


def build_count_statement(
    *, table: str, columns: list[str], criteria: dict[str, Any], dialect: str
) -> Optional[Select[Any]]:
    """``SELECT COUNT(*)`` over the rows ``criteria`` selects (no cursor predicate)."""
    if not supports_filters(table, criteria) or any(name not in columns for name in criteria):
        return None
    source = _table(table, columns)
    statement = select(func.count()).select_from(source)
    for name, value in criteria.items():
        statement = statement.where(binary_ordered(source.c[name], dialect) == value)
    return statement
