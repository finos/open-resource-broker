"""REST contract for the list cursor: round trip, rejection, and offset fallback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from orb.api.dependencies import (
    get_list_requests_orchestrator,
    get_request_formatter,
    get_scheduler_strategy,
)
from orb.api.routers.requests import router as requests_router
from orb.application.queries.request_query_handlers import ListRequestsHandler
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.domain.base.entity import PersistedRevision
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType
from orb.domain.services.generic_filter_service import GenericFilterService
from orb.infrastructure.error.exception_handler import get_exception_handler

BASE = datetime(2026, 5, 1, tzinfo=timezone.utc)


def _requests(count: int) -> list[Request]:
    rows = []
    for index in range(count):
        rows.append(
            Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id="tmpl-1",
                machine_count=1,
                provider_type="aws",
                provider_name="aws-default",
                provider_api="RunInstances",
            ).model_copy(
                update={
                    "created_at": BASE + timedelta(minutes=index),
                    "persisted_revision": PersistedRevision(),
                }
            )
        )
    return rows


class _Uow:
    def __init__(self, rows: list[Request]) -> None:
        self.requests = MagicMock()
        self.requests.find_all.return_value = rows
        self.machines = MagicMock()

    def __enter__(self) -> "_Uow":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _UowFactory:
    def __init__(self, rows: list[Request]) -> None:
        self._rows = rows

    def create_unit_of_work(self) -> _Uow:
        return _Uow(self._rows)


class _Bus:
    def __init__(self, handler: ListRequestsHandler) -> None:
        self._handler = handler

    async def execute(self, query):  # noqa: ANN001
        return await self._handler.execute_query(query)


@pytest.fixture
def client_and_rows():
    rows = _requests(7)
    handler = ListRequestsHandler(
        uow_factory=_UowFactory(rows),  # type: ignore[arg-type]
        logger=MagicMock(),
        error_handler=MagicMock(),
        generic_filter_service=GenericFilterService(),
    )
    orchestrator = ListRequestsOrchestrator(MagicMock(), _Bus(handler), MagicMock())  # type: ignore[arg-type]
    formatter = MagicMock()
    formatter.format_request_status.side_effect = lambda requests, total_count, next_cursor: (
        MagicMock(
            data={
                "requests": [r["request_id"] for r in requests],
                "total_count": total_count,
                "next_cursor": next_cursor,
            }
        )
    )

    app = FastAPI()
    app.include_router(requests_router)
    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def handle(_request, exc):  # noqa: ANN001
        response = exception_handler.handle_error_for_http(exc)
        return JSONResponse(status_code=response.http_status or 500, content={"detail": "x"})

    app.dependency_overrides[get_list_requests_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_request_formatter] = lambda: formatter
    app.dependency_overrides[get_scheduler_strategy] = lambda: MagicMock()
    return TestClient(app, raise_server_exceptions=False), rows


@pytest.mark.unit
@pytest.mark.api
class TestListCursorContract:
    def test_cursor_walk_returns_every_row_once(self, client_and_rows) -> None:
        client, rows = client_and_rows
        expected = [
            str(r.request_id) for r in sorted(rows, key=lambda r: r.created_at, reverse=True)
        ]

        seen: list[str] = []
        cursor = None
        while True:
            params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
            body = client.get("/requests/", params=params).json()
            seen.extend(body["requests"])
            cursor = body["next_cursor"]
            if cursor is None:
                break

        assert seen == expected

    def test_a_cursor_for_another_filter_is_a_400(self, client_and_rows) -> None:
        client, _ = client_and_rows
        cursor = client.get("/requests/", params={"limit": 2, "status": "pending"}).json()[
            "next_cursor"
        ]

        response = client.get(
            "/requests/", params={"limit": 2, "status": "complete", "cursor": cursor}
        )

        assert response.status_code == 400

    def test_a_cursor_for_another_sort_is_a_400(self, client_and_rows) -> None:
        client, _ = client_and_rows
        cursor = client.get("/requests/", params={"limit": 2, "sort": "request_id"}).json()[
            "next_cursor"
        ]

        response = client.get(
            "/requests/", params={"limit": 2, "sort": "-request_id", "cursor": cursor}
        )

        assert response.status_code == 400

    @pytest.mark.parametrize("cursor", ["garbage", "e30=", "eyJ2IjoyfQ=="])
    def test_garbage_is_a_400_not_the_first_page(self, client_and_rows, cursor: str) -> None:
        client, _ = client_and_rows

        assert client.get("/requests/", params={"cursor": cursor}).status_code == 400

    def test_legacy_offset_cursor_keeps_working(self, client_and_rows) -> None:
        from orb.application.services.orchestration.dtos import encode_cursor

        client, rows = client_and_rows
        newest_first = [
            str(r.request_id) for r in sorted(rows, key=lambda r: r.created_at, reverse=True)
        ]

        body = client.get("/requests/", params={"limit": 3, "cursor": encode_cursor(2)}).json()

        assert body["requests"] == newest_first[2:5]

    def test_changing_only_the_limit_keeps_the_cursor_valid(self, client_and_rows) -> None:
        client, rows = client_and_rows
        newest_first = [
            str(r.request_id) for r in sorted(rows, key=lambda r: r.created_at, reverse=True)
        ]
        cursor = client.get("/requests/", params={"limit": 2}).json()["next_cursor"]

        body = client.get("/requests/", params={"limit": 4, "cursor": cursor}).json()

        assert body["requests"] == newest_first[2:6]
