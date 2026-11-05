"""Keyset paging primitives: ordering, cursors positions and stability."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

import pytest

from orb.domain.base.pagination import (
    SortSpec,
    paginate_in_memory,
    row_position,
    sort_text,
)


@dataclass
class Row:
    id: str
    status: Any = None
    name: Optional[str] = None


class Color(str, Enum):
    RED = "red"
    BLUE = "blue"


def _ids(rows: list[Row]) -> list[str]:
    return [r.id for r in rows]


def _walk(rows: list[Row], sort: str, limit: int) -> list[str]:
    spec = SortSpec.parse(sort)
    assert spec is not None
    seen: list[str] = []
    after = None
    while True:
        page = paginate_in_memory(rows, spec, after, limit, id_of=lambda r: r.id)
        seen.extend(_ids(page.items))
        if page.next_position is None:
            return seen
        after = page.next_position


@pytest.mark.unit
class TestSortText:
    def test_none_is_empty(self) -> None:
        assert sort_text(None) == ""

    def test_enum_sorts_by_value(self) -> None:
        assert sort_text(Color.RED) == "red"

    def test_datetimes_use_the_stored_iso_form(self) -> None:
        whole = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        micro = datetime(2026, 1, 1, 12, 0, 0, 5, tzinfo=timezone.utc)

        assert sort_text(whole) == "2026-01-01T12:00:00+00:00"  # no fraction when zero
        assert sort_text(micro) == "2026-01-01T12:00:00.000005+00:00"
        assert sort_text(datetime(2026, 1, 1, 12, 0)) == "2026-01-01T12:00:00"

    def test_json_stored_datetimes_write_utc_as_z(self) -> None:
        whole = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        micro = datetime(2026, 1, 1, 12, 0, 0, 5, tzinfo=timezone.utc)
        east = datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=5)))

        assert sort_text(whole, json_datetimes=True) == "2026-01-01T12:00:00Z"
        assert sort_text(micro, json_datetimes=True) == "2026-01-01T12:00:00.000005Z"
        assert sort_text(east, json_datetimes=True) == "2026-01-01T12:00:00+05:00"

    def test_text_matches_what_the_serializers_write(self) -> None:
        from pydantic import BaseModel

        class Stored(BaseModel):
            at: datetime

        for value in (
            datetime(2026, 3, 1, 12, tzinfo=timezone.utc),
            datetime(2026, 3, 1, 12, 0, 0, 5, tzinfo=timezone.utc),
            datetime(2026, 3, 1, 12, 0, 0, 123000, tzinfo=timezone.utc),
            datetime(2026, 3, 1, 12, tzinfo=timezone(timedelta(hours=5))),
        ):
            assert (
                sort_text(value, json_datetimes=True)
                == Stored(at=value).model_dump(mode="json")["at"]
            )
            assert sort_text(value) == value.isoformat()

    def test_text_and_numbers_use_str(self) -> None:
        assert sort_text("Abc") == "Abc"
        assert sort_text(7) == "7"


@pytest.mark.unit
class TestSortSpec:
    @pytest.mark.parametrize(
        ("text", "attr", "descending"),
        [("status", "status", False), ("+status", "status", False), ("-status", "status", True)],
    )
    def test_parse(self, text: str, attr: str, descending: bool) -> None:
        assert SortSpec.parse(text) == SortSpec(attr, descending)

    @pytest.mark.parametrize("text", [None, "", "-", "+"])
    def test_no_sort(self, text: Optional[str]) -> None:
        assert SortSpec.parse(text) is None

    def test_round_trips_through_text(self) -> None:
        assert str(SortSpec.parse("-created_at")) == "-created_at"


@pytest.mark.unit
class TestPaginateInMemory:
    def test_orders_by_sort_text_then_id_byte_wise(self) -> None:
        rows = [Row("b", "x"), Row("a", "x"), Row("C", "x"), Row("c", "x")]

        page = paginate_in_memory(rows, SortSpec("status"), None, None, lambda r: r.id)

        assert _ids(page.items) == ["C", "a", "b", "c"]  # uppercase sorts before lowercase

    def test_mixed_case_sort_values_are_byte_ordered(self) -> None:
        rows = [Row("1", "running"), Row("2", "Running"), Row("3", "RUNNING")]

        assert _walk(rows, "status", 1) == ["3", "2", "1"]

    def test_descending_reverses_ties_too(self) -> None:
        rows = [Row("a", "x"), Row("b", "x"), Row("c", "y")]

        assert _walk(rows, "-status", 2) == ["c", "b", "a"]

    def test_none_and_empty_values_sort_first_ascending(self) -> None:
        rows = [Row("1", "b"), Row("2", None), Row("3", ""), Row("4", "a")]

        assert _walk(rows, "status", 1) == ["2", "3", "4", "1"]

    def test_unknown_attribute_orders_by_id(self) -> None:
        rows = [Row("b"), Row("a"), Row("c")]

        assert _walk(rows, "nope", 2) == ["a", "b", "c"]

    def test_limit_plus_one_detects_the_last_page(self) -> None:
        rows = [Row(str(i)) for i in range(5)]
        spec = SortSpec("id")

        first = paginate_in_memory(rows, spec, None, 5, lambda r: r.id)
        short = paginate_in_memory(rows, spec, None, 4, lambda r: r.id)

        assert first.has_more is False and first.next_position is None
        assert short.has_more is True and short.next_position == ("3", "3")

    def test_limit_none_returns_everything_after_the_position(self) -> None:
        rows = [Row(str(i)) for i in range(5)]

        page = paginate_in_memory(rows, SortSpec("id"), ("1", "1"), None, lambda r: r.id)

        assert _ids(page.items) == ["2", "3", "4"]

    def test_limit_zero_is_an_empty_page(self) -> None:
        page = paginate_in_memory([Row("a")], SortSpec("id"), None, 0, lambda r: r.id)

        assert page.items == [] and page.has_more is True

    def test_skip_applies_after_the_cursor_filter(self) -> None:
        rows = [Row(str(i)) for i in range(6)]

        page = paginate_in_memory(rows, SortSpec("id"), None, 2, lambda r: r.id, skip=3)

        assert _ids(page.items) == ["3", "4"]

    def test_row_inserted_before_the_boundary_does_not_repeat_or_skip(self) -> None:
        rows = [Row(chr(ord("b") + i)) for i in range(0, 8, 2)]  # b d f h
        spec = SortSpec("id")
        first = paginate_in_memory(rows, spec, None, 2, lambda r: r.id)

        rows.append(Row("a"))  # lands before the page boundary
        second = paginate_in_memory(rows, spec, first.next_position, 2, lambda r: r.id)

        assert _ids(first.items) == ["b", "d"]
        assert _ids(second.items) == ["f", "h"]

    def test_row_deleted_at_the_boundary_does_not_hide_the_next(self) -> None:
        rows = [Row("a"), Row("b"), Row("c"), Row("d")]
        spec = SortSpec("id")
        first = paginate_in_memory(rows, spec, None, 2, lambda r: r.id)

        rows = [r for r in rows if r.id != "b"]  # the boundary row vanishes
        second = paginate_in_memory(rows, spec, first.next_position, 2, lambda r: r.id)

        assert _ids(second.items) == ["c", "d"]

    def test_positions_match_row_position(self) -> None:
        rows = [Row("a", Color.RED)]
        page = paginate_in_memory(rows, SortSpec("status"), None, 5, lambda r: r.id)

        assert page.positions == [row_position(rows[0], SortSpec("status"), lambda r: r.id)]
        assert page.positions == [("red", "a")]
