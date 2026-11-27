"""Creation-time range through the real orchestrator, handler and storage backends."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from orb.application.queries.request_query_handlers import ListRequestsHandler
from orb.application.services.orchestration.dtos import ListRequestsInput
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType
from orb.domain.services.generic_filter_service import GenericFilterService

BASE_TIME = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(params=["json", "sql"])
def make_uow(request, tmp_path: Path):
    if request.param == "json":
        from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork

        yield lambda: JSONUnitOfWork(data_dir=str(tmp_path), backup_enabled=False)
    else:
        from sqlalchemy import create_engine

        from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

        engine = create_engine(f"sqlite:///{tmp_path / 'orb.db'}")
        yield lambda: SQLUnitOfWork(engine)
        engine.dispose()


class _UowFactory:
    def __init__(self, make: Callable[[], Any]) -> None:
        self._make = make

    def create_unit_of_work(self) -> Any:
        return self._make()


class _Bus:
    def __init__(self, handler: Any) -> None:
        self._handler = handler

    async def execute(self, query: Any) -> Any:
        return await self._handler.execute_query(query)


@pytest.fixture
def orchestrator(make_uow):
    handler = ListRequestsHandler(
        uow_factory=_UowFactory(make_uow),  # type: ignore[arg-type]
        logger=MagicMock(),
        error_handler=MagicMock(),
        generic_filter_service=GenericFilterService(),
    )
    return ListRequestsOrchestrator(MagicMock(), _Bus(handler), MagicMock())  # type: ignore[arg-type]


def _seed(make_uow: Callable[[], Any]) -> dict[int, str]:
    """Seven requests created one hour apart; returns hour offset -> request id."""
    ids: dict[int, str] = {}
    with make_uow() as uow:
        for hour in range(7):
            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id="tmpl-1",
                machine_count=1,
                provider_type="aws",
                provider_name="aws-default",
                provider_api="RunInstances",
            ).model_copy(update={"created_at": BASE_TIME + timedelta(hours=hour)})
            uow.requests.save(request)
            ids[hour] = str(request.request_id)
    return ids


def _list(orchestrator: Any, **kwargs: Any) -> Any:
    return asyncio.run(orchestrator.execute(ListRequestsInput(**kwargs)))


@pytest.mark.integration
class TestDateRangeEndToEnd:
    # Hours 2..5 are in range: 14:00Z to 17:00Z
    START = "2026-03-01T14:00:00Z"
    END = "2026-03-01T17:00:00Z"

    def test_cursor_walk_stays_in_range_and_accepts_an_equivalent_offset(
        self, make_uow, orchestrator
    ):
        ids = _seed(make_uow)
        in_range = [ids[h] for h in (5, 4, 3, 2)]  # newest first

        page1 = _list(orchestrator, limit=2, start_time=self.START, end_time=self.END)
        assert [r["request_id"] for r in page1.requests] == in_range[:2]
        assert page1.total_count == 4
        assert page1.next_cursor

        # The same instants written with a +02:00 offset must accept the cursor
        page2 = _list(
            orchestrator,
            limit=2,
            cursor=page1.next_cursor,
            start_time="2026-03-01T16:00:00+02:00",
            end_time="2026-03-01T19:00:00+02:00",
        )
        assert [r["request_id"] for r in page2.requests] == in_range[2:]
        assert page2.total_count == 4
        assert page2.next_cursor is None

        walked = [r["request_id"] for r in page1.requests + page2.requests]
        assert len(walked) == len(set(walked)) == 4

    def test_bounds_are_inclusive(self, make_uow, orchestrator):
        ids = _seed(make_uow)

        out = _list(orchestrator, start_time=self.START, end_time=self.START)

        assert [r["request_id"] for r in out.requests] == [ids[2]]

    def test_no_range_lists_everything(self, make_uow, orchestrator):
        _seed(make_uow)

        assert _list(orchestrator).total_count == 7
