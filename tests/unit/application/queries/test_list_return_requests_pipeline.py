"""Unit tests for SyncAndListReturnRequestsHandler.

Covers the read-through sync loop and the DTO filter/search/sort/pagination
pipeline in application/queries/request_query_handlers.py that the existing
active-requests tests do not reach.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from orb.application.dto.queries import SyncAndListReturnRequestsQuery


def _make_return_request(request_id="ret-1", terminal=False, machine_ids=None):
    status = SimpleNamespace(
        is_terminal=lambda: terminal,
        value="completed" if terminal else "in_progress",
    )
    return SimpleNamespace(
        request_id=SimpleNamespace(value=request_id),
        status=status,
        machine_ids=machine_ids or [],
    )


def _make_dto(request_id, provider_name="aws", provider_type="aws", template_id="tmpl"):
    class _DTO:
        def __init__(self):
            self.request_id = request_id
            self.provider_name = provider_name
            self.provider_type = provider_type
            self.template_id = template_id

        def model_dump(self):
            return {
                "request_id": self.request_id,
                "provider_name": self.provider_name,
                "provider_type": self.provider_type,
                "template_id": self.template_id,
            }

    return _DTO()


def _build_handler(return_requests, dtos, filter_side_effect=None, sync_side_effect=None):
    from orb.application.queries.request_query_handlers import (
        SyncAndListReturnRequestsHandler,
    )

    mock_uow = MagicMock()
    mock_uow.requests.find_by_type.return_value = return_requests
    mock_uow.machines.find_by_ids.return_value = []
    mock_uow.__enter__ = lambda s: s
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow_factory = MagicMock()
    mock_uow_factory.create_unit_of_work.return_value = mock_uow

    mock_filter_service = MagicMock()
    mock_filter_service.apply_filters.side_effect = filter_side_effect or (lambda items, _: items)

    machine_sync = MagicMock()
    machine_sync.fetch_provider_machines = AsyncMock(
        side_effect=sync_side_effect, return_value=([], {})
    )
    machine_sync.sync_machines_with_provider = AsyncMock(return_value=([], []))

    with patch("orb.application.queries.request_query_handlers.RequestDTOFactory") as MockFactory:
        dto_iter = iter(dtos)
        MockFactory.return_value.create_from_domain.side_effect = lambda req, machines: next(
            dto_iter
        )
        handler = SyncAndListReturnRequestsHandler(
            uow_factory=mock_uow_factory,
            logger=MagicMock(),
            error_handler=MagicMock(),
            generic_filter_service=mock_filter_service,
            machine_sync_service=machine_sync,
        )

    status_service = MagicMock()
    status_service.determine_status_from_machines.return_value = (None, None)
    status_service.update_request_status = AsyncMock()
    handler._status_service = status_service

    query_service = MagicMock()
    query_service.get_machines_for_request = AsyncMock(return_value=[])
    handler._query_service = query_service

    return handler, status_service


class TestSyncAndListReturnRequests:
    def test_terminal_requests_skip_sync(self):
        reqs = [_make_return_request("ret-1", terminal=True)]
        handler, status_service = _build_handler(reqs, [_make_dto("ret-1")])
        result = asyncio.run(handler.execute_query(SyncAndListReturnRequestsQuery()))
        assert result.total_count == 1
        status_service.update_request_status.assert_not_awaited()

    def test_non_terminal_request_synced_and_status_updated(self):
        reqs = [_make_return_request("ret-1", terminal=False)]
        handler, status_service = _build_handler(reqs, [_make_dto("ret-1")])
        status_service.determine_status_from_machines.return_value = (
            "completed",
            "done",
        )
        result = asyncio.run(handler.execute_query(SyncAndListReturnRequestsQuery()))
        assert result.total_count == 1
        status_service.update_request_status.assert_awaited_once()

    def test_sync_failure_is_swallowed(self):
        reqs = [_make_return_request("ret-1", terminal=False)]
        handler, _ = _build_handler(
            reqs,
            [_make_dto("ret-1")],
            sync_side_effect=RuntimeError("provider down"),
        )
        # Must not raise; stored state returned.
        result = asyncio.run(handler.execute_query(SyncAndListReturnRequestsQuery()))
        assert result.total_count == 1

    def test_filter_by_provider_name(self):
        reqs = [
            _make_return_request("ret-1", terminal=True),
            _make_return_request("ret-2", terminal=True),
        ]
        dtos = [
            _make_dto("ret-1", provider_name="aws"),
            _make_dto("ret-2", provider_name="gcp"),
        ]
        handler, _ = _build_handler(reqs, dtos)
        result = asyncio.run(
            handler.execute_query(SyncAndListReturnRequestsQuery(provider_name="aws"))
        )
        assert result.total_count == 1
        assert result.items[0].request_id == "ret-1"

    def test_filter_by_provider_type(self):
        reqs = [
            _make_return_request("ret-1", terminal=True),
            _make_return_request("ret-2", terminal=True),
        ]
        dtos = [
            _make_dto("ret-1", provider_type="aws"),
            _make_dto("ret-2", provider_type="k8s"),
        ]
        handler, _ = _build_handler(reqs, dtos)
        result = asyncio.run(
            handler.execute_query(SyncAndListReturnRequestsQuery(provider_type="k8s"))
        )
        assert result.total_count == 1
        assert result.items[0].request_id == "ret-2"

    def test_free_text_search(self):
        reqs = [
            _make_return_request("ret-1", terminal=True),
            _make_return_request("ret-2", terminal=True),
        ]
        dtos = [
            _make_dto("ret-1", template_id="web"),
            _make_dto("ret-2", template_id="db"),
        ]
        handler, _ = _build_handler(reqs, dtos)
        result = asyncio.run(handler.execute_query(SyncAndListReturnRequestsQuery(q="web")))
        assert result.total_count == 1
        assert result.items[0].request_id == "ret-1"

    def test_sort_descending(self):
        reqs = [
            _make_return_request("ret-1", terminal=True),
            _make_return_request("ret-2", terminal=True),
        ]
        dtos = [
            _make_dto("ret-1", template_id="aaa"),
            _make_dto("ret-2", template_id="zzz"),
        ]
        handler, _ = _build_handler(reqs, dtos)
        result = asyncio.run(
            handler.execute_query(SyncAndListReturnRequestsQuery(sort="-template_id"))
        )
        assert [i.request_id for i in result.items] == ["ret-2", "ret-1"]

    def test_offset_and_limit(self):
        reqs = [_make_return_request(f"ret-{i}", terminal=True) for i in range(4)]
        dtos = [_make_dto(f"ret-{i}", template_id=f"t{i}") for i in range(4)]
        handler, _ = _build_handler(reqs, dtos)
        result = asyncio.run(
            handler.execute_query(
                SyncAndListReturnRequestsQuery(sort="+template_id", offset=1, limit=2)
            )
        )
        assert result.total_count == 4
        assert [i.request_id for i in result.items] == ["ret-1", "ret-2"]

    def test_machine_names_filter(self):
        reqs = [
            _make_return_request("ret-1", terminal=True),
            _make_return_request("ret-2", terminal=True),
        ]

        class _DTOWithMachines:
            def __init__(self, request_id, machine_name):
                self.request_id = request_id
                self.provider_name = "aws"
                self.provider_type = "aws"
                self._machine_name = machine_name

            def model_dump(self):
                return {"machines": [{"name": self._machine_name}]}

        dtos = [
            _DTOWithMachines("ret-1", "keep-me"),
            _DTOWithMachines("ret-2", "drop-me"),
        ]
        handler, _ = _build_handler(reqs, dtos)
        result = asyncio.run(
            handler.execute_query(SyncAndListReturnRequestsQuery(machine_names=["keep-me"]))
        )
        assert result.total_count == 1
        assert result.items[0].request_id == "ret-1"


class TestKeysetPaging:
    """Return requests page by (sort text, id) after a cursor position."""

    @staticmethod
    def _handler(ids):
        reqs = [_make_return_request(i, terminal=True) for i in ids]
        handler, _ = _build_handler(reqs, [_make_dto(i) for i in ids])
        return handler

    def test_walk_by_cursor_position_visits_each_row_once(self):
        ids = ["ret-e", "ret-b", "ret-d", "ret-a", "ret-c"]
        seen = []
        after = None
        for _ in range(5):
            handler = self._handler(ids)
            page = asyncio.run(
                handler.execute_query(
                    SyncAndListReturnRequestsQuery(limit=2, sort="request_id", after=after)
                )
            )
            seen.extend(d.request_id for d in page.items)
            after = page.next_position
            if after is None:
                break

        assert seen == sorted(ids)
        assert page.total_count == 5

    def test_descending_and_last_page_has_no_position(self):
        handler = self._handler(["ret-a", "ret-b", "ret-c"])

        first = asyncio.run(
            handler.execute_query(SyncAndListReturnRequestsQuery(limit=2, sort="-request_id"))
        )
        handler = self._handler(["ret-a", "ret-b", "ret-c"])
        last = asyncio.run(
            handler.execute_query(
                SyncAndListReturnRequestsQuery(
                    limit=2, sort="-request_id", after=first.next_position
                )
            )
        )

        assert [d.request_id for d in first.items] == ["ret-c", "ret-b"]
        assert [d.request_id for d in last.items] == ["ret-a"]
        assert last.next_position is None

    def test_without_a_sort_the_old_offset_slice_applies(self):
        handler = self._handler(["ret-a", "ret-b", "ret-c"])

        page = asyncio.run(handler.execute_query(SyncAndListReturnRequestsQuery(limit=1, offset=1)))

        assert [d.request_id for d in page.items] == ["ret-b"]
        assert not hasattr(page, "next_position")
