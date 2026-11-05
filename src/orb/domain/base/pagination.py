"""Keyset pagination primitives shared by every storage backend.

A page is addressed by the last row of the previous page: its sort value and its
id. Rows are ordered by ``(sort value, id)``, both compared as text and both in
the requested direction, so the order is total and a row that is inserted or
deleted between two pages can neither repeat nor hide another row.

The text a row sorts by comes from ``sort_text``. Every backend must order by
the same text, whether it sorts in process (``paginate_in_memory``) or in the
database, otherwise page 2 would not continue where page 1 ended.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Generic, Optional, TypeVar

T = TypeVar("T")

# (sort value as text, row id as text)
Position = tuple[str, str]


def sort_text(value: Any, *, json_datetimes: bool = False) -> str:
    """The text a value sorts by: the form it has in storage.

    ``None`` is the empty string, so missing values sort first when ascending.
    Enums sort by their value. Datetimes sort by their ISO-8601 text exactly as
    the storage layer writes it, because the database orders by that stored text
    and an in-process pager must agree with it row for row: no fraction when the
    microsecond is zero, six digits otherwise, the original UTC offset. Entities
    that are stored through pydantic's JSON mode (machines) write a UTC offset as
    ``Z``; pass ``json_datetimes=True`` for them. Text order equals chronological
    order for timestamps in one offset, with sub-second ties sorting after a
    whole-second value of the same second.
    """
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        text = value.isoformat()
        if json_datetimes and text.endswith("+00:00"):
            text = text[: -len("+00:00")] + "Z"
        return text
    return str(value)


@dataclass(frozen=True)
class SortSpec:
    """A sort order such as ``-created_at`` (descending) or ``status``."""

    attr: str
    descending: bool = False

    @classmethod
    def parse(cls, sort: Optional[str]) -> Optional["SortSpec"]:
        """The spec for ``"+field"`` / ``"-field"`` / ``"field"``; None when empty."""
        if not sort:
            return None
        text = str(sort)
        attr = text.lstrip("-+")
        if not attr:
            return None
        return cls(attr=attr, descending=text.startswith("-"))

    def __str__(self) -> str:
        return ("-" if self.descending else "") + self.attr


@dataclass
class PageSlice(Generic[T]):
    """One page cut from an ordered sequence."""

    items: list[T]
    positions: list[Position]
    has_more: bool

    @property
    def next_position(self) -> Optional[Position]:
        """Where the following page starts after, or None on the last page."""
        return self.positions[-1] if self.has_more and self.positions else None


@dataclass
class StoragePage(Generic[T]):
    """A page a storage backend cut itself (database push-down)."""

    items: list[T]
    positions: list[Position]
    has_more: bool
    total_count: int
    # Position of the last row the database returned, including rows that could
    # not be loaded and are absent from ``items``; the next page continues there.
    last_position: Optional[Position] = None

    @property
    def next_position(self) -> Optional[Position]:
        if not self.has_more:
            return None
        if self.last_position is not None:
            return self.last_position
        return self.positions[-1] if self.positions else None


def row_position(
    item: Any, spec: SortSpec, id_of: Callable[[Any], str], json_datetimes: bool = False
) -> Position:
    """The ``(sort text, id)`` pair that orders ``item``."""
    return sort_text(getattr(item, spec.attr, ""), json_datetimes=json_datetimes), id_of(item)


def paginate_in_memory(
    items: Sequence[T],
    spec: SortSpec,
    after: Optional[Position],
    limit: Optional[int],
    id_of: Callable[[T], str],
    skip: int = 0,
    json_datetimes: bool = False,
) -> PageSlice[T]:
    """Order ``items`` by ``(sort text, id)`` and cut the page after ``after``.

    ``limit`` of None returns everything after ``after``; ``skip`` drops that many
    rows first (the offset of a caller that has no cursor yet). This holds the whole
    sequence in memory: backends that cannot sort in the database (JSON,
    DynamoDB) page this way, with the same order and cursors as the database.
    """
    keyed = sorted(
        ((row_position(item, spec, id_of, json_datetimes), item) for item in items),
        key=lambda pair: pair[0],
        reverse=spec.descending,
    )
    if after is not None:
        if spec.descending:
            keyed = [pair for pair in keyed if pair[0] < after]
        else:
            keyed = [pair for pair in keyed if pair[0] > after]

    if skip > 0:
        keyed = keyed[skip:]

    has_more = False
    if limit is not None:
        has_more = len(keyed) > limit
        keyed = keyed[: max(limit, 0)]

    return PageSlice(
        items=[item for _, item in keyed],
        positions=[position for position, _ in keyed],
        has_more=has_more,
    )
