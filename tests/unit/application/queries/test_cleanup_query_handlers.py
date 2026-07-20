"""Unit tests for application/queries/cleanup_query_handlers.py."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from orb.application.queries.cleanup_query_handlers import (
    ListCleanableRequestsHandler,
    ListCleanableRequestsQuery,
    ListCleanableResourcesHandler,
    ListCleanableResourcesQuery,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(request_id="req-1", created_at=None, status="complete"):
    r = MagicMock()
    r.request_id = request_id
    r.status = status
    r.created_at = created_at
    return r


def _make_machine(machine_id="m-1", request_id="req-1", status="running"):
    m = MagicMock()
    m.machine_id = machine_id
    m.request_id = request_id
    m.status = status
    return m


def _make_uow_factory(requests=None, machines=None):
    uow = MagicMock()
    uow.requests.list_all.return_value = requests if requests is not None else []
    uow.machines.list_all.return_value = machines if machines is not None else []

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = _create
    return factory


def _make_logger():
    return MagicMock()


def _make_error_handler():
    return MagicMock()


# ---------------------------------------------------------------------------
# ListCleanableRequestsHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListCleanableRequestsHandler:
    def _handler(self, requests=None):
        return ListCleanableRequestsHandler(
            uow_factory=_make_uow_factory(requests=requests),
            logger=_make_logger(),
            error_handler=_make_error_handler(),
        )

    @pytest.mark.asyncio
    async def test_empty_list_when_no_requests(self):
        h = self._handler(requests=[])
        q = ListCleanableRequestsQuery(older_than_days=30)
        result = await h.execute_query(q)
        assert result["status"] == "success"
        assert result["total_count"] == 0
        assert result["cleanable_requests"] == []

    @pytest.mark.asyncio
    async def test_old_request_included(self):
        old_ts = datetime.now(timezone.utc) - timedelta(days=90)
        req = _make_request(request_id="req-old", created_at=old_ts)
        h = self._handler(requests=[req])
        q = ListCleanableRequestsQuery(older_than_days=30)
        result = await h.execute_query(q)
        assert result["status"] == "success"
        assert result["total_count"] == 1
        ids = [r["request_id"] for r in result["cleanable_requests"]]
        assert "req-old" in ids

    @pytest.mark.asyncio
    async def test_recent_request_excluded(self):
        new_ts = datetime.now(timezone.utc) - timedelta(days=1)
        req = _make_request(request_id="req-new", created_at=new_ts)
        h = self._handler(requests=[req])
        q = ListCleanableRequestsQuery(older_than_days=30)
        result = await h.execute_query(q)
        assert result["total_count"] == 0

    @pytest.mark.asyncio
    async def test_request_without_created_at_excluded(self):
        req = _make_request(request_id="req-no-ts", created_at=None)
        h = self._handler(requests=[req])
        q = ListCleanableRequestsQuery(older_than_days=30)
        result = await h.execute_query(q)
        assert result["total_count"] == 0

    @pytest.mark.asyncio
    async def test_age_days_returned_in_result(self):
        old_ts = datetime.now(timezone.utc) - timedelta(days=100)
        req = _make_request(request_id="req-1", created_at=old_ts)
        h = self._handler(requests=[req])
        q = ListCleanableRequestsQuery(older_than_days=10)
        result = await h.execute_query(q)
        item = result["cleanable_requests"][0]
        assert item["age_days"] >= 99

    @pytest.mark.asyncio
    async def test_cutoff_date_returned(self):
        h = self._handler(requests=[])
        q = ListCleanableRequestsQuery(older_than_days=7)
        result = await h.execute_query(q)
        assert "cutoff_date" in result
        assert result["older_than_days"] == 7

    @pytest.mark.asyncio
    async def test_exception_returns_error_status(self):
        factory = MagicMock()
        factory.create_unit_of_work.side_effect = RuntimeError("db error")
        h = ListCleanableRequestsHandler(
            uow_factory=factory,
            logger=_make_logger(),
            error_handler=_make_error_handler(),
        )
        q = ListCleanableRequestsQuery(older_than_days=30)
        result = await h.execute_query(q)
        assert result["status"] == "error"
        assert "db error" in result["error"]
        assert result["total_count"] == 0


# ---------------------------------------------------------------------------
# ListCleanableResourcesHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListCleanableResourcesHandler:
    def _handler(self, requests=None, machines=None):
        return ListCleanableResourcesHandler(
            uow_factory=_make_uow_factory(requests=requests, machines=machines),
            logger=_make_logger(),
            error_handler=_make_error_handler(),
        )

    @pytest.mark.asyncio
    async def test_no_orphans_when_all_requests_exist(self):
        req = _make_request(request_id="req-1")
        machine = _make_machine(machine_id="m-1", request_id="req-1")
        h = self._handler(requests=[req], machines=[machine])
        q = ListCleanableResourcesQuery()
        result = await h.execute_query(q)
        assert result["status"] == "success"
        assert result["orphaned_count"] == 0

    @pytest.mark.asyncio
    async def test_orphaned_machine_detected(self):
        # Machine references a request that does NOT exist
        machine = _make_machine(machine_id="m-orphan", request_id="req-missing")
        h = self._handler(requests=[], machines=[machine])
        q = ListCleanableResourcesQuery()
        result = await h.execute_query(q)
        assert result["status"] == "success"
        assert result["orphaned_count"] == 1
        assert result["orphaned_machines"][0]["machine_id"] == "m-orphan"

    @pytest.mark.asyncio
    async def test_machine_without_request_id_excluded(self):
        machine = MagicMock()
        machine.machine_id = "m-noreq"
        machine.request_id = None
        machine.status = "running"
        h = self._handler(requests=[], machines=[machine])
        q = ListCleanableResourcesQuery()
        result = await h.execute_query(q)
        assert result["orphaned_count"] == 0

    @pytest.mark.asyncio
    async def test_totals_returned(self):
        req = _make_request("req-1")
        m1 = _make_machine("m-1", "req-1")
        m2 = _make_machine("m-2", "req-missing")
        h = self._handler(requests=[req], machines=[m1, m2])
        q = ListCleanableResourcesQuery()
        result = await h.execute_query(q)
        assert result["total_machines"] == 2
        assert result["total_requests"] == 1
        assert result["orphaned_count"] == 1

    @pytest.mark.asyncio
    async def test_exception_returns_error_status(self):
        factory = MagicMock()
        factory.create_unit_of_work.side_effect = RuntimeError("db exploded")
        h = ListCleanableResourcesHandler(
            uow_factory=factory,
            logger=_make_logger(),
            error_handler=_make_error_handler(),
        )
        q = ListCleanableResourcesQuery()
        result = await h.execute_query(q)
        assert result["status"] == "error"
        assert "db exploded" in result["error"]
        assert result["orphaned_count"] == 0
