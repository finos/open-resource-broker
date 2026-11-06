"""Keyset paging through the real orchestrators, handlers and storage backends.

The same walks run on JSON, SQL (SQLite) and DynamoDB (moto). Each is checked
against an order computed independently in the test, which also proves that
every backend produces the same id sequence for the same data.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

moto = pytest.importorskip("moto")

from orb.application.dto.responses import MachineDTO  # noqa: E402
from orb.application.queries.machine_query_handlers import ListMachinesHandler  # noqa: E402
from orb.application.queries.request_query_handlers import ListRequestsHandler  # noqa: E402
from orb.application.services.orchestration.dtos import (  # noqa: E402
    ListMachinesInput,
    ListRequestsInput,
    decode_cursor,
    encode_cursor,
)
from orb.application.services.orchestration.list_machines import (  # noqa: E402
    ListMachinesOrchestrator,
)
from orb.application.services.orchestration.list_requests import (  # noqa: E402
    ListRequestsOrchestrator,
)
from orb.domain.base.exceptions import ValidationError  # noqa: E402
from orb.domain.base.pagination import sort_text  # noqa: E402
from orb.domain.machine.aggregate import Machine  # noqa: E402
from orb.domain.machine.machine_identifiers import MachineId  # noqa: E402
from orb.domain.machine.machine_status import MachineStatus  # noqa: E402
from orb.domain.request.aggregate import Request  # noqa: E402
from orb.domain.request.request_types import RequestStatus, RequestType  # noqa: E402
from orb.domain.services.generic_filter_service import GenericFilterService  # noqa: E402

BASE_TIME = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
MACHINE_IDS = [
    "i-b2",
    "i-B2",
    "i-a1",
    "i-A1",
    "i-c3",
    "i-C3",
    "i-d4",
    "i-D4",
    "i-e5",
    "i-E5",
    "i-f6",
    "i-F6",
    "i-g7",
]


@pytest.fixture(params=["json", "sql", "dynamodb"])
def make_uow(request, tmp_path: Path):
    """A factory of fresh units of work over one shared store."""
    name = request.param
    if name == "json":
        from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork

        yield lambda: JSONUnitOfWork(data_dir=str(tmp_path), backup_enabled=False)
    elif name == "sql":
        from sqlalchemy import create_engine

        from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

        engine = create_engine(f"sqlite:///{tmp_path / 'orb.db'}")
        yield lambda: SQLUnitOfWork(engine)
        engine.dispose()
    else:
        from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
        from orb.providers.aws.storage.unit_of_work import DynamoDBUnitOfWork

        with moto.mock_aws():
            yield lambda: DynamoDBUnitOfWork(
                aws_client=None, logger=LoggingAdapter("test.keyset"), region="us-east-1"
            )
    request.node.backend = name


class _UowFactory:
    def __init__(self, make_uow: Callable[[], Any]) -> None:
        self._make = make_uow

    def create_unit_of_work(self) -> Any:
        return self._make()


class _Bus:
    def __init__(self, handler: Any) -> None:
        self._handler = handler

    async def execute(self, query: Any) -> Any:
        return await self._handler.execute_query(query)


@pytest.fixture
def machines_orchestrator(make_uow):
    handler = ListMachinesHandler(
        uow_factory=_UowFactory(make_uow),  # type: ignore[arg-type]
        logger=MagicMock(),
        error_handler=MagicMock(),
        command_bus=MagicMock(),
        generic_filter_service=GenericFilterService(),
        machine_sync_service=MagicMock(),
    )
    return ListMachinesOrchestrator(MagicMock(), _Bus(handler), MagicMock())  # type: ignore[arg-type]


@pytest.fixture
def requests_orchestrator(make_uow):
    handler = ListRequestsHandler(
        uow_factory=_UowFactory(make_uow),  # type: ignore[arg-type]
        logger=MagicMock(),
        error_handler=MagicMock(),
        generic_filter_service=GenericFilterService(),
    )
    return ListRequestsOrchestrator(MagicMock(), _Bus(handler), MagicMock())  # type: ignore[arg-type]


def _machine(
    machine_id: str,
    status: MachineStatus = MachineStatus.RUNNING,
    launched: datetime | None = None,
    created: datetime | None = None,
    request_id: str = "req-00000000-0000-4000-8000-000000000001",
) -> Machine:
    return Machine(
        machine_id=machine_id,
        template_id="tmpl-1",
        request_id=request_id,
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
        instance_type="t3.medium",
        image_id="ami-1",
        status=status,
        launch_time=launched,
        created_at=created or BASE_TIME,
    )


def _seed_machines(make_uow: Callable[[], Any]) -> dict[str, Machine]:
    """13 machines: mixed-case ids, ties on status, some without a launch time."""
    seeded: dict[str, Machine] = {}
    statuses = [MachineStatus.RUNNING, MachineStatus.PENDING, MachineStatus.STOPPED]
    with make_uow() as uow:
        for index, machine_id in enumerate(MACHINE_IDS):
            machine = _machine(
                machine_id,
                status=statuses[index % 3],
                launched=None if index % 4 == 0 else BASE_TIME + timedelta(minutes=index // 2),
                created=BASE_TIME + timedelta(seconds=index // 3),
            )
            uow.machines.save(machine)
            seeded[machine_id] = machine
    return seeded


def _expected(rows: dict[str, Machine], attr: str, descending: bool) -> list[str]:
    keyed = sorted(
        ((sort_text(getattr(m, attr), json_datetimes=True), mid) for mid, m in rows.items()),
        reverse=descending,
    )
    return [mid for _, mid in keyed]


def _walk_machines(
    orchestrator: Any, limit: int = 4, **filters: Any
) -> tuple[list[str], list[int]]:
    ids: list[str] = []
    totals: list[int] = []
    cursor = None
    for _ in range(50):
        out = asyncio.run(
            orchestrator.execute(ListMachinesInput(limit=limit, cursor=cursor, **filters))
        )
        ids.extend(m.machine_id for m in out.machines)
        totals.append(out.total_count)
        cursor = out.next_cursor
        if cursor is None:
            return ids, totals
    raise AssertionError("cursor chain did not end")


@pytest.mark.integration
class TestMachinePaging:
    @pytest.mark.parametrize(
        ("sort", "attr", "descending"),
        [
            ("machine_id", "machine_id", False),
            ("-machine_id", "machine_id", True),
            ("status", "status", False),
            ("-launch_time", "launch_time", True),
            ("launch_time", "launch_time", False),
            ("-created_at", "created_at", True),
        ],
    )
    def test_walk_visits_every_row_once_in_byte_order(
        self, make_uow, machines_orchestrator, sort, attr, descending
    ):
        rows = _seed_machines(make_uow)

        ids, totals = _walk_machines(machines_orchestrator, sort=sort)

        assert ids == _expected(rows, attr, descending)
        assert set(totals) == {len(rows)}

    def test_filtered_walk_pages_only_matching_rows(self, make_uow, machines_orchestrator):
        rows = _seed_machines(make_uow)
        running = {k: m for k, m in rows.items() if m.status == MachineStatus.RUNNING}

        ids, totals = _walk_machines(machines_orchestrator, sort="machine_id", status="running")

        assert ids == _expected(running, "machine_id", False)
        assert set(totals) == {len(running)}

    def test_page_sizes_are_respected(self, make_uow, machines_orchestrator):
        _seed_machines(make_uow)

        first = asyncio.run(
            machines_orchestrator.execute(ListMachinesInput(limit=5, sort="machine_id"))
        )

        assert first.count == 5 and first.next_cursor is not None

    def test_row_added_and_boundary_row_removed_between_pages(
        self, make_uow, machines_orchestrator
    ):
        rows = _seed_machines(make_uow)
        order = _expected(rows, "machine_id", False)
        first = asyncio.run(
            machines_orchestrator.execute(ListMachinesInput(limit=4, sort="machine_id"))
        )
        boundary = order[3]
        with make_uow() as uow:
            uow.machines.save(_machine("i-0new"))  # sorts before every other id
            uow.machines.delete(MachineId(value=boundary))

        rest: list[str] = []
        cursor = first.next_cursor
        while cursor:
            out = asyncio.run(
                machines_orchestrator.execute(
                    ListMachinesInput(limit=4, sort="machine_id", cursor=cursor)
                )
            )
            rest.extend(m.machine_id for m in out.machines)
            cursor = out.next_cursor

        assert rest == order[4:]  # no repeat of the first page, no gap after it

    def test_cursor_of_another_query_is_rejected(self, make_uow, machines_orchestrator):
        _seed_machines(make_uow)
        out = asyncio.run(
            machines_orchestrator.execute(
                ListMachinesInput(limit=3, sort="machine_id", status="running")
            )
        )

        for other in (
            {"status": "pending"},
            {"sort": "-machine_id", "status": "running"},
            {"status": "running", "provider_type": "aws"},
            {"status": "running", "q": "i-"},
        ):
            params = {"sort": "machine_id", **other}
            with pytest.raises(ValidationError):
                asyncio.run(
                    machines_orchestrator.execute(
                        ListMachinesInput(limit=3, cursor=out.next_cursor, **params)
                    )
                )

    def test_changing_only_the_limit_keeps_the_cursor_valid(self, make_uow, machines_orchestrator):
        rows = _seed_machines(make_uow)
        first = asyncio.run(
            machines_orchestrator.execute(ListMachinesInput(limit=3, sort="machine_id"))
        )

        second = asyncio.run(
            machines_orchestrator.execute(
                ListMachinesInput(limit=6, sort="machine_id", cursor=first.next_cursor)
            )
        )

        order = _expected(rows, "machine_id", False)
        assert [m.machine_id for m in second.machines] == order[3:9]

    def test_garbage_cursor_is_an_error_not_the_first_page(self, make_uow, machines_orchestrator):
        _seed_machines(make_uow)

        with pytest.raises(ValidationError):
            asyncio.run(machines_orchestrator.execute(ListMachinesInput(cursor="garbage")))

    def test_legacy_offset_cursor_still_pages(self, make_uow, machines_orchestrator):
        rows = _seed_machines(make_uow)

        out = asyncio.run(
            machines_orchestrator.execute(
                ListMachinesInput(limit=4, sort="machine_id", cursor=encode_cursor(4))
            )
        )

        assert [m.machine_id for m in out.machines] == _expected(rows, "machine_id", False)[4:8]

    def test_cursor_keeps_the_cumulative_offset_for_older_releases(
        self, make_uow, machines_orchestrator
    ):
        _seed_machines(make_uow)
        first = asyncio.run(
            machines_orchestrator.execute(ListMachinesInput(limit=4, sort="machine_id"))
        )
        second = asyncio.run(
            machines_orchestrator.execute(
                ListMachinesInput(limit=4, sort="machine_id", cursor=first.next_cursor)
            )
        )

        assert decode_cursor(first.next_cursor) == 4
        assert decode_cursor(second.next_cursor) == 8

    def test_offset_without_a_cursor_still_skips(self, make_uow, machines_orchestrator):
        rows = _seed_machines(make_uow)

        out = asyncio.run(
            machines_orchestrator.execute(ListMachinesInput(limit=3, offset=5, sort="machine_id"))
        )

        assert [m.machine_id for m in out.machines] == _expected(rows, "machine_id", False)[5:8]

    def test_unfiltered_total_is_not_the_page(self, make_uow, machines_orchestrator):
        _seed_machines(make_uow)

        out = asyncio.run(
            machines_orchestrator.execute(ListMachinesInput(limit=2, sort="machine_id"))
        )

        assert out.count == 2 and out.total_count == len(MACHINE_IDS)
        assert isinstance(out.machines[0], MachineDTO)


def _seed_one_second(make_uow: Callable[[], Any], mixed_microseconds: bool) -> dict[str, Machine]:
    """12 machines launched within one second (whole-second AWS launch times)."""
    seeded: dict[str, Machine] = {}
    with make_uow() as uow:
        for index in range(12):
            micro = 5 if mixed_microseconds and index % 4 == 0 else 0
            machine = _machine(f"i-{index:02d}", launched=BASE_TIME.replace(microsecond=micro))
            uow.machines.save(machine)
            seeded[machine.machine_id.value] = machine
    return seeded


def _continue(orchestrator: Any, first: Any, **params: Any) -> list[str]:
    ids = [m.machine_id for m in first.machines]
    cursor = first.next_cursor
    while cursor:
        out = asyncio.run(orchestrator.execute(ListMachinesInput(cursor=cursor, **params)))
        ids.extend(m.machine_id for m in out.machines)
        cursor = out.next_cursor
    return ids


@pytest.mark.integration
class TestWalkStartedInProcessAndContinuedByCursor:
    """The first page of a walk may be cut in process (offset, legacy cursor) and the
    rest in the database; both must order by the same stored text."""

    @pytest.mark.parametrize("mixed", [False, True], ids=["whole-seconds", "mixed-microseconds"])
    @pytest.mark.parametrize("start", ["offset", "legacy-cursor"])
    def test_every_remaining_row_comes_back_exactly_once(
        self, make_uow, machines_orchestrator, start: str, mixed: bool
    ):
        rows = _seed_one_second(make_uow, mixed)
        order = _expected(rows, "launch_time", True)
        params: dict[str, Any] = {"limit": 3, "sort": "-launch_time"}
        first_input = (
            ListMachinesInput(offset=3, **params)
            if start == "offset"
            else ListMachinesInput(cursor=encode_cursor(3), **params)
        )

        first = asyncio.run(machines_orchestrator.execute(first_input))
        ids = _continue(machines_orchestrator, first, **params)

        assert ids == order[3:]

    @pytest.mark.parametrize("sort", ["launch_time", "-launch_time", "-created_at"])
    def test_mixed_microseconds_order_alike_on_every_backend(
        self, make_uow, machines_orchestrator, sort: str
    ):
        rows = _seed_one_second(make_uow, mixed_microseconds=True)

        ids, _ = _walk_machines(machines_orchestrator, limit=5, sort=sort)

        attr = sort.lstrip("-")
        assert ids == _expected(rows, attr, sort.startswith("-"))


@pytest.mark.integration
class TestDatabasePushDown:
    def test_sql_cuts_the_page_in_the_database_and_others_page_in_process(
        self, request, make_uow, machines_orchestrator, monkeypatch
    ):
        from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

        calls: list[tuple[Any, ...]] = []
        real = SQLStorageStrategy.find_page

        def spy(self, criteria, sort_column, descending, after, limit):
            calls.append((criteria, sort_column, descending, after, limit))
            return real(self, criteria, sort_column, descending, after, limit)

        monkeypatch.setattr(SQLStorageStrategy, "find_page", spy)
        _seed_machines(make_uow)

        _walk_machines(machines_orchestrator, sort="machine_id", status="running")

        sample = make_uow()
        is_sql = type(sample).__name__ == "SQLUnitOfWork"
        assert bool(calls) is is_sql
        if is_sql:
            assert all(c[0] == {"status": "running"} and c[1] == "machine_id" for c in calls)
            assert calls[0][3] is None and all(c[3] is not None for c in calls[1:])

    def test_query_the_database_cannot_answer_pages_in_process(
        self, make_uow, machines_orchestrator, monkeypatch
    ):
        from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

        calls: list[Any] = []
        monkeypatch.setattr(SQLStorageStrategy, "find_page", lambda *a, **k: calls.append(1))
        rows = _seed_machines(make_uow)

        ids, _ = _walk_machines(machines_orchestrator, sort="machine_id", q="i-")

        assert calls == []  # free-text search never reaches the database
        assert ids == _expected(rows, "machine_id", False)


def _seed_requests(make_uow: Callable[[], Any]) -> dict[str, Request]:
    seeded: dict[str, Request] = {}
    statuses = [RequestStatus.PENDING, RequestStatus.COMPLETED, RequestStatus.IN_PROGRESS]
    with make_uow() as uow:
        for index in range(11):
            base = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=f"tmpl-{index % 2}",
                machine_count=1,
                provider_type="aws",
                provider_name="aws-default",
                provider_api="RunInstances",
            )
            request = base.model_copy(
                update={
                    "created_at": BASE_TIME + timedelta(minutes=index // 3),  # ties of three
                    "status": statuses[index % 3],
                }
            )
            uow.requests.save(request)
            seeded[str(request.request_id)] = request
    return seeded


@pytest.mark.integration
class TestRequestPaging:
    def test_default_sort_walks_newest_first_with_id_ties(self, make_uow, requests_orchestrator):
        rows = _seed_requests(make_uow)
        expected = [
            rid
            for _, rid in sorted(
                ((sort_text(r.created_at), rid) for rid, r in rows.items()), reverse=True
            )
        ]

        ids: list[str] = []
        cursor = None
        while True:
            out = asyncio.run(
                requests_orchestrator.execute(ListRequestsInput(limit=4, cursor=cursor))
            )
            ids.extend(r["request_id"] for r in out.requests)
            cursor = out.next_cursor
            if cursor is None:
                break

        assert ids == expected

    def test_filtered_walk_on_template(self, make_uow, requests_orchestrator):
        rows = _seed_requests(make_uow)
        wanted = {rid for rid, r in rows.items() if r.template_id == "tmpl-1"}

        seen: list[str] = []
        cursor = None
        while True:
            out = asyncio.run(
                requests_orchestrator.execute(
                    ListRequestsInput(limit=2, template_id="tmpl-1", sort="status", cursor=cursor)
                )
            )
            seen.extend(r["request_id"] for r in out.requests)
            cursor = out.next_cursor
            if cursor is None:
                break

        assert set(seen) == wanted and len(seen) == len(wanted)

    def test_cursor_of_another_filter_is_rejected(self, make_uow, requests_orchestrator):
        _seed_requests(make_uow)
        out = asyncio.run(
            requests_orchestrator.execute(ListRequestsInput(limit=2, template_id="tmpl-1"))
        )

        with pytest.raises(ValidationError):
            asyncio.run(
                requests_orchestrator.execute(
                    ListRequestsInput(limit=2, template_id="tmpl-0", cursor=out.next_cursor)
                )
            )
