"""ListRequestsHandler created_at range filter."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from orb.application.queries.request_query_handlers import ListRequestsHandler
from orb.application.request.queries import ListRequestsQuery
from orb.domain.base.pagination import StoragePage

BASE = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


def _req(request_id: str, created_at):
    return SimpleNamespace(
        request_id=request_id,
        request_type=None,
        template_id="t",
        provider_api="RunInstances",
        provider_name="aws",
        provider_type="aws",
        status=None,
        machine_ids=[],
        created_at=created_at,
    )


def _requests():
    # r0 at BASE, r1 a day later, r2 two days later, r3 three days later
    return [_req(f"r{i}", BASE + timedelta(days=i)) for i in range(4)]


def _run(uow_requests, query):
    uow = MagicMock()
    uow.requests = uow_requests
    uow.machines.find_by_ids.return_value = []
    uow.__enter__ = lambda s: s
    uow.__exit__ = MagicMock(return_value=False)
    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    with patch("orb.application.queries.request_query_handlers.RequestDTOFactory") as dto:
        dto.return_value.create_from_domain.side_effect = lambda r, m: SimpleNamespace(
            request_id=r.request_id
        )
        handler = ListRequestsHandler(
            uow_factory=factory,
            logger=MagicMock(),
            error_handler=MagicMock(),
            generic_filter_service=MagicMock(),
        )
        return asyncio.run(handler.execute_query(query))


def _store(items):
    # find_page is absent from a plain MagicMock spec so in-memory paging is used
    repo = MagicMock(spec=["find_all"])
    repo.find_all.return_value = items
    return repo


def _ids(result):
    return [i.request_id for i in result.items]


class TestRange:
    def test_start_only(self):
        result = _run(_store(_requests()), ListRequestsQuery(start_time=BASE + timedelta(days=2)))
        assert _ids(result) == ["r2", "r3"]
        assert result.total_count == 2

    def test_end_only(self):
        result = _run(_store(_requests()), ListRequestsQuery(end_time=BASE + timedelta(days=1)))
        assert _ids(result) == ["r0", "r1"]
        assert result.total_count == 2

    def test_both_bounds_are_inclusive(self):
        query = ListRequestsQuery(
            start_time=BASE + timedelta(days=1), end_time=BASE + timedelta(days=2)
        )
        result = _run(_store(_requests()), query)
        assert _ids(result) == ["r1", "r2"]

    def test_just_outside_bounds_excluded(self):
        eps = timedelta(microseconds=1)
        query = ListRequestsQuery(
            start_time=BASE + timedelta(days=1) + eps, end_time=BASE + timedelta(days=2) - eps
        )
        assert _ids(_run(_store(_requests()), query)) == []

    def test_naive_created_at_is_utc(self):
        items = [_req("naive", BASE.replace(tzinfo=None))]
        query = ListRequestsQuery(start_time=BASE, end_time=BASE)
        assert _ids(_run(_store(items), query)) == ["naive"]

    def test_non_utc_created_at_compared_as_instant(self):
        plus2 = timezone(timedelta(hours=2))
        items = [_req("tz", BASE.astimezone(plus2))]
        query = ListRequestsQuery(start_time=BASE, end_time=BASE)
        assert _ids(_run(_store(items), query)) == ["tz"]

    def test_missing_created_at_excluded_when_ranged(self):
        items = [_req("none", None)]
        assert _ids(_run(_store(items), ListRequestsQuery(start_time=BASE))) == []

    def test_no_range_returns_everything(self):
        assert len(_run(_store(_requests()), ListRequestsQuery()).items) == 4

    def test_total_unfiltered_still_counts_all(self):
        result = _run(_store(_requests()), ListRequestsQuery(start_time=BASE + timedelta(days=3)))
        assert (result.total_count, result.total_unfiltered) == (1, 4)


class TestSortAndCursor:
    def test_cursor_pages_stay_in_range_without_gaps_or_duplicates(self):
        items = [_req(f"r{i}", BASE + timedelta(hours=i)) for i in range(10)]
        # in range: hours 2..7 -> r2..r7 (6 rows)
        start, end = BASE + timedelta(hours=2), BASE + timedelta(hours=7)
        repo = _store(items)

        page1 = _run(
            repo, ListRequestsQuery(sort="created_at", limit=4, start_time=start, end_time=end)
        )
        assert _ids(page1) == ["r2", "r3", "r4", "r5"]
        assert page1.total_count == 6
        assert page1.next_position is not None

        page2 = _run(
            repo,
            ListRequestsQuery(
                sort="created_at",
                limit=4,
                start_time=start,
                end_time=end,
                after=page1.next_position,
            ),
        )
        assert _ids(page2) == ["r6", "r7"]
        assert page2.total_count == 6
        assert page2.next_position is None


class TestPushDown:
    @staticmethod
    def _sql_like(items):
        repo = MagicMock(spec=["find_all", "find_page"])
        repo.find_all.return_value = items
        repo.find_page.return_value = StoragePage(
            items=items, positions=[], has_more=False, total_count=len(items)
        )
        return repo

    def test_range_does_not_use_find_page_and_is_applied(self):
        repo = self._sql_like(_requests())
        query = ListRequestsQuery(sort="created_at", limit=10, start_time=BASE + timedelta(days=2))
        result = _run(repo, query)
        repo.find_page.assert_not_called()
        assert _ids(result) == ["r2", "r3"]

    def test_without_range_find_page_is_still_used(self):
        repo = self._sql_like(_requests())
        _run(repo, ListRequestsQuery(sort="created_at", limit=10))
        repo.find_page.assert_called_once()
