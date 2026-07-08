"""Tests for ListActiveRequestsHandler — per-task sync timeout behaviour."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from orb.application.dto.queries import ListActiveRequestsQuery
from orb.application.queries.request_query_handlers import ListActiveRequestsHandler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQ_ID_A = "req-00000000-0000-0000-0000-000000000001"
_REQ_ID_B = "req-00000000-0000-0000-0000-000000000002"
_REQ_ID_C = "req-00000000-0000-0000-0000-000000000003"


def _make_fake_request(request_id: str = _REQ_ID_A, machine_ids: list[str] | None = None):
    """Return a minimal request-like namespace accepted by ListActiveRequestsHandler."""
    from orb.domain.request.value_objects import RequestStatus

    return SimpleNamespace(
        request_id=SimpleNamespace(value=request_id),
        machine_ids=machine_ids or [],
        status=RequestStatus.IN_PROGRESS,
        template_id="tmpl-1",
        request_type=SimpleNamespace(value="acquire"),
        provider_type="aws",
    )


def _build_handler(
    requests: list,
    machine_sync_side_effect=None,
    sync_timeout: float = 1.0,
) -> tuple[ListActiveRequestsHandler, MagicMock]:
    """Build a ListActiveRequestsHandler with all collaborators mocked.

    Returns (handler, mock_logger).

    machine_sync_side_effect: passed to populate_missing_machine_ids as side_effect.
    sync_timeout: injected directly into handler._sync_timeout so tests run fast.
    """
    mock_logger = Mock()
    mock_error_handler = Mock()

    # UoW — returns the provided request list
    mock_uow = MagicMock()
    mock_uow.requests.find_all.return_value = requests
    mock_uow.__enter__ = lambda s: s
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow_factory = MagicMock()
    mock_uow_factory.create_unit_of_work.return_value = mock_uow

    mock_filter_service = MagicMock()
    mock_filter_service.apply_filters.side_effect = lambda items, _: items

    mock_machine_sync = MagicMock()
    mock_machine_sync.populate_missing_machine_ids = AsyncMock(side_effect=machine_sync_side_effect)
    mock_machine_sync.fetch_provider_machines = AsyncMock(return_value=([], {}))
    mock_machine_sync.sync_machines_with_provider = AsyncMock(return_value=([], []))

    with patch("orb.application.queries.request_query_handlers.RequestDTOFactory") as MockFactory:
        mock_dto_factory = MagicMock()
        MockFactory.return_value = mock_dto_factory
        mock_dto_factory.create_from_domain.side_effect = lambda req, _machines: SimpleNamespace(
            request_id=str(req.request_id.value),
        )

        handler = ListActiveRequestsHandler(
            uow_factory=mock_uow_factory,
            logger=mock_logger,
            error_handler=mock_error_handler,
            generic_filter_service=mock_filter_service,
            machine_sync_service=mock_machine_sync,
            config=None,
        )

    # Replace internal services after construction so DTO creation works
    mock_query_service = AsyncMock()
    # Return the same fake request on every get_request / get_machines call
    mock_query_service.get_request = AsyncMock(
        side_effect=lambda rid: next(
            (r for r in requests if str(r.request_id.value) == rid), requests[0]
        )
    )
    mock_query_service.get_machines_for_request = AsyncMock(return_value=[])
    handler._query_service = mock_query_service

    mock_status_service = Mock()
    mock_status_service.determine_status_from_machines = Mock(return_value=(None, None))
    mock_status_service.update_request_status = AsyncMock()
    handler._status_service = mock_status_service

    # Inject a short timeout so tests don't actually wait 30 s
    handler._sync_timeout = sync_timeout

    return handler, mock_logger


# ---------------------------------------------------------------------------
# Timeout path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_timeout_logs_warning_and_returns_stored_state():
    """When _do_sync never resolves, wait_for raises TimeoutError.

    Expected behaviour:
    - WARNING is logged with request_id, machine_ids, and timeout_seconds
    - The handler does NOT raise; it returns a DTO built from stored state
    """
    req = _make_fake_request(_REQ_ID_A, machine_ids=["i-abc123"])

    async def _stuck(*_args, **_kwargs):
        # Never completes — simulates a stalled AWS call
        await asyncio.sleep(9999)

    handler, mock_logger = _build_handler(
        requests=[req],
        machine_sync_side_effect=_stuck,
        sync_timeout=0.05,  # 50 ms so the test is fast
    )

    query = ListActiveRequestsQuery(all_resources=True)
    result = await handler.execute_query(query)

    # Must succeed (no exception propagated)
    assert result is not None
    assert hasattr(result, "items")

    # WARNING must have been logged for the timed-out request
    warning_calls = mock_logger.warning.call_args_list
    assert any(
        _REQ_ID_A in str(call) and "timed out" in str(call).lower() for call in warning_calls
    ), f"Expected a timeout warning containing {_REQ_ID_A!r}, got: {warning_calls}"


@pytest.mark.asyncio
async def test_sync_timeout_does_not_propagate_exception():
    """TimeoutError from wait_for is caught and does NOT bubble up to the caller."""
    req = _make_fake_request(_REQ_ID_A)

    async def _stuck(*_args, **_kwargs):
        await asyncio.sleep(9999)

    handler, _ = _build_handler(
        requests=[req],
        machine_sync_side_effect=_stuck,
        sync_timeout=0.05,
    )

    # Must not raise
    query = ListActiveRequestsQuery(all_resources=True)
    result = await handler.execute_query(query)
    assert result is not None


@pytest.mark.asyncio
async def test_other_tasks_complete_when_one_times_out():
    """Tasks B and C complete normally even when task A is permanently stuck.

    This verifies that a single stuck sync does not block the semaphore slots
    from being released, allowing the rest of the batch to complete.
    """
    req_a = _make_fake_request(_REQ_ID_A, machine_ids=["i-stuck"])
    req_b = _make_fake_request(_REQ_ID_B)
    req_c = _make_fake_request(_REQ_ID_C)

    completed: list[str] = []

    async def _selective_stuck(request, *_args, **_kwargs):
        if request.request_id.value == _REQ_ID_A:
            await asyncio.sleep(9999)
        else:
            completed.append(str(request.request_id.value))

    handler, mock_logger = _build_handler(
        requests=[req_a, req_b, req_c],
        machine_sync_side_effect=_selective_stuck,
        sync_timeout=0.05,
    )

    query = ListActiveRequestsQuery(all_resources=True)
    result = await handler.execute_query(query)

    # All three requests are returned (stored state for A, synced state for B/C)
    assert result is not None
    assert len(result.items) == 3

    # B and C must have completed their sync
    assert _REQ_ID_B in completed
    assert _REQ_ID_C in completed

    # A must have triggered a timeout warning
    warning_calls = mock_logger.warning.call_args_list
    assert any("timed out" in str(c).lower() and _REQ_ID_A in str(c) for c in warning_calls)


# ---------------------------------------------------------------------------
# Config injection tests
# ---------------------------------------------------------------------------


def test_resolve_sync_timeout_uses_config_when_provided():
    """_resolve_sync_timeout reads performance.sync_timeout_seconds from app_config."""
    mock_perf = SimpleNamespace(sync_timeout_seconds=42.5)
    mock_app_cfg = SimpleNamespace(performance=mock_perf)
    mock_config = Mock()
    mock_config.app_config = mock_app_cfg

    with patch("orb.application.queries.request_query_handlers.RequestDTOFactory"):
        handler = ListActiveRequestsHandler(
            uow_factory=MagicMock(),
            logger=Mock(),
            error_handler=Mock(),
            generic_filter_service=MagicMock(),
            machine_sync_service=MagicMock(),
            config=mock_config,
        )

    assert handler._sync_timeout == pytest.approx(42.5)


def test_resolve_sync_timeout_falls_back_to_default_when_config_is_none():
    """When config=None is passed, the default 30 s timeout is used."""
    with patch("orb.application.queries.request_query_handlers.RequestDTOFactory"):
        handler = ListActiveRequestsHandler(
            uow_factory=MagicMock(),
            logger=Mock(),
            error_handler=Mock(),
            generic_filter_service=MagicMock(),
            machine_sync_service=MagicMock(),
            config=None,
        )

    assert handler._sync_timeout == pytest.approx(30.0)


def test_resolve_sync_timeout_falls_back_to_default_when_config_raises():
    """If app_config raises, _resolve_sync_timeout silently falls back to default."""
    mock_config = Mock()
    mock_config.app_config = property(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))

    with patch("orb.application.queries.request_query_handlers.RequestDTOFactory"):
        handler = ListActiveRequestsHandler(
            uow_factory=MagicMock(),
            logger=Mock(),
            error_handler=Mock(),
            generic_filter_service=MagicMock(),
            machine_sync_service=MagicMock(),
            config=mock_config,
        )

    assert handler._sync_timeout == pytest.approx(30.0)
