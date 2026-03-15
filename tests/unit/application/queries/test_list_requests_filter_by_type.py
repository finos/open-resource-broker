"""Tests for --request-type filter on ListRequestsQuery and ListRequestsHandler."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from orb.application.request.queries import ListRequestsQuery
from orb.domain.request.request_types import RequestType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(request_type: RequestType, request_id: str = "req-1"):
    r = SimpleNamespace(
        request_id=SimpleNamespace(value=request_id),
        request_type=request_type,
        template_id="tmpl-1",
        requested_count=1,
        status=SimpleNamespace(value="pending"),
        provider_api=None,
        provider_name=None,
        provider_type="aws",
        machine_ids=[],
        resource_ids=[],
        metadata={},
        created_at=None,
        updated_at=None,
        error_details=None,
        desired_capacity=1,
    )
    return r


# ---------------------------------------------------------------------------
# Query DTO tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_list_requests_query_request_type_defaults_to_none():
    q = ListRequestsQuery()
    assert q.request_type is None


@pytest.mark.unit
def test_list_requests_query_accepts_acquire():
    q = ListRequestsQuery(request_type="acquire")
    assert q.request_type == "acquire"


@pytest.mark.unit
def test_list_requests_query_accepts_return():
    q = ListRequestsQuery(request_type="return")
    assert q.request_type == "return"


# ---------------------------------------------------------------------------
# Handler filter tests (unit — no DB, no DI)
# ---------------------------------------------------------------------------


def _run_handler_with_requests(all_requests, query):
    """Run ListRequestsHandler.execute_query with a mocked UoW returning all_requests."""
    from orb.application.queries.request_query_handlers import ListRequestsHandler

    mock_logger = MagicMock()
    mock_error_handler = MagicMock()
    mock_filter_service = MagicMock()
    mock_filter_service.apply_filters.side_effect = lambda items, _: items

    mock_uow = MagicMock()
    mock_uow.requests.find_all.return_value = all_requests
    mock_uow.machines.find_by_ids.return_value = []
    mock_uow.__enter__ = lambda s: s
    mock_uow.__exit__ = MagicMock(return_value=False)

    mock_uow_factory = MagicMock()
    mock_uow_factory.create_unit_of_work.return_value = mock_uow

    handler = ListRequestsHandler(
        uow_factory=mock_uow_factory,
        logger=mock_logger,
        error_handler=mock_error_handler,
        generic_filter_service=mock_filter_service,
    )

    # Patch RequestDTOFactory so we don't need full domain wiring
    with patch(
        "orb.application.factories.request_dto_factory.RequestDTOFactory"
    ) as MockFactory:
        mock_dto_factory = MagicMock()
        MockFactory.return_value = mock_dto_factory
        mock_dto_factory.create_from_domain.side_effect = lambda req, machines: SimpleNamespace(
            request_id=str(req.request_id.value),
            request_type=req.request_type.value,
        )

        import asyncio

        return asyncio.get_event_loop().run_until_complete(handler.execute_query(query))


@pytest.mark.unit
def test_list_requests_filter_by_acquire_type():
    acquire = _make_request(RequestType.ACQUIRE, "acq-1")
    ret = _make_request(RequestType.RETURN, "ret-1")

    results = _run_handler_with_requests(
        [acquire, ret], ListRequestsQuery(request_type="acquire")
    )

    assert len(results) == 1
    assert results[0].request_type == "acquire"


@pytest.mark.unit
def test_list_requests_filter_by_return_type():
    acquire = _make_request(RequestType.ACQUIRE, "acq-1")
    ret = _make_request(RequestType.RETURN, "ret-1")

    results = _run_handler_with_requests(
        [acquire, ret], ListRequestsQuery(request_type="return")
    )

    assert len(results) == 1
    assert results[0].request_type == "return"


@pytest.mark.unit
def test_list_requests_no_filter_returns_all():
    acquire = _make_request(RequestType.ACQUIRE, "acq-1")
    ret = _make_request(RequestType.RETURN, "ret-1")

    results = _run_handler_with_requests([acquire, ret], ListRequestsQuery())

    assert len(results) == 2


# ---------------------------------------------------------------------------
# CLI argparse test
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_list_requests_invalid_type_not_accepted_by_cli():
    from orb.cli.args import parse_args

    import sys

    original_argv = sys.argv
    try:
        sys.argv = ["orb", "requests", "list", "--request-type", "invalid"]
        with pytest.raises(SystemExit) as exc_info:
            parse_args()
        assert exc_info.value.code != 0
    finally:
        sys.argv = original_argv


@pytest.mark.unit
def test_list_requests_cli_accepts_acquire():
    from orb.cli.args import parse_args

    import sys

    original_argv = sys.argv
    try:
        sys.argv = ["orb", "requests", "list", "--request-type", "acquire"]
        args, _ = parse_args()
        assert args.request_type == "acquire"
    finally:
        sys.argv = original_argv


@pytest.mark.unit
def test_list_requests_cli_accepts_return():
    from orb.cli.args import parse_args

    import sys

    original_argv = sys.argv
    try:
        sys.argv = ["orb", "requests", "list", "--request-type", "return"]
        args, _ = parse_args()
        assert args.request_type == "return"
    finally:
        sys.argv = original_argv
