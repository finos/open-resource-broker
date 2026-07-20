"""Unit tests for request_command_handlers — handle_get_multiple_requests.

Covers lines 503-535:
- happy path with request_ids from various arg name variants
- no IDs returns error dict
- QueryBus.execute result serialization
"""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_ns(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_container_with_query_bus(result=None):
    from orb.infrastructure.di.buses import QueryBus

    container = MagicMock()
    query_bus = AsyncMock(spec=QueryBus)
    query_bus.execute.return_value = result or MagicMock(
        requests=[],
        found_count=0,
        not_found_ids=[],
        total_requested=0,
    )

    container.get.side_effect = lambda t: query_bus if t is QueryBus else MagicMock()
    return container, query_bus


@pytest.mark.unit
class TestHandleGetMultipleRequests:
    @pytest.mark.asyncio
    async def test_no_ids_returns_error(self):
        """No request IDs in any arg → error dict returned."""
        from orb.interface.request_command_handlers import handle_get_multiple_requests

        container, _ = _make_container_with_query_bus()
        args = _make_ns(_container=container)

        result = await handle_get_multiple_requests(args)

        assert isinstance(result, dict)
        assert "error" in result
        assert "No request IDs" in result["error"]

    @pytest.mark.asyncio
    async def test_request_ids_from_args_request_ids(self):
        """IDs from args.request_ids are forwarded to QueryBus."""
        from orb.interface.request_command_handlers import handle_get_multiple_requests

        mock_result = MagicMock()
        mock_result.requests = []
        mock_result.found_count = 0
        mock_result.not_found_ids = []
        mock_result.total_requested = 2

        container, query_bus = _make_container_with_query_bus(result=mock_result)
        args = _make_ns(_container=container, request_ids=["r-1", "r-2"])

        result = await handle_get_multiple_requests(args)

        query_bus.execute.assert_awaited_once()
        query_arg = query_bus.execute.call_args[0][0]
        assert "r-1" in query_arg.request_ids
        assert "r-2" in query_arg.request_ids
        assert isinstance(result, dict)
        assert "requests" in result

    @pytest.mark.asyncio
    async def test_request_ids_from_flag_request_ids(self):
        """IDs from args.flag_request_ids are forwarded to QueryBus."""
        from orb.interface.request_command_handlers import handle_get_multiple_requests

        mock_result = MagicMock()
        mock_result.requests = []
        mock_result.found_count = 0
        mock_result.not_found_ids = []
        mock_result.total_requested = 1

        container, query_bus = _make_container_with_query_bus(result=mock_result)
        args = _make_ns(_container=container, flag_request_ids=["r-xyz"])

        await handle_get_multiple_requests(args)

        query_arg = query_bus.execute.call_args[0][0]
        assert "r-xyz" in query_arg.request_ids

    @pytest.mark.asyncio
    async def test_request_ids_from_flag_ids(self):
        """IDs from args.flag_ids are forwarded to QueryBus."""
        from orb.interface.request_command_handlers import handle_get_multiple_requests

        mock_result = MagicMock()
        mock_result.requests = []
        mock_result.found_count = 1
        mock_result.not_found_ids = []
        mock_result.total_requested = 1

        container, query_bus = _make_container_with_query_bus(result=mock_result)
        args = _make_ns(_container=container, flag_ids=["r-abc"])

        await handle_get_multiple_requests(args)

        query_arg = query_bus.execute.call_args[0][0]
        assert "r-abc" in query_arg.request_ids

    @pytest.mark.asyncio
    async def test_include_machines_default_true(self):
        """include_machines defaults to True when not set in args."""
        from orb.interface.request_command_handlers import handle_get_multiple_requests

        mock_result = MagicMock()
        mock_result.requests = []
        mock_result.found_count = 0
        mock_result.not_found_ids = []
        mock_result.total_requested = 1

        container, query_bus = _make_container_with_query_bus(result=mock_result)
        args = _make_ns(_container=container, request_ids=["r-1"])

        await handle_get_multiple_requests(args)

        query_arg = query_bus.execute.call_args[0][0]
        assert query_arg.include_machines is True

    @pytest.mark.asyncio
    async def test_include_machines_false_when_set(self):
        """include_machines=False is forwarded to query."""
        from orb.interface.request_command_handlers import handle_get_multiple_requests

        mock_result = MagicMock()
        mock_result.requests = []
        mock_result.found_count = 0
        mock_result.not_found_ids = []
        mock_result.total_requested = 1

        container, query_bus = _make_container_with_query_bus(result=mock_result)
        args = _make_ns(_container=container, request_ids=["r-1"], include_machines=False)

        await handle_get_multiple_requests(args)

        query_arg = query_bus.execute.call_args[0][0]
        assert query_arg.include_machines is False

    @pytest.mark.asyncio
    async def test_result_requests_serialized_with_model_dump(self):
        """Requests with model_dump are serialized via model_dump()."""
        from orb.interface.request_command_handlers import handle_get_multiple_requests

        req = MagicMock()
        req.model_dump.return_value = {"request_id": "r-1", "status": "pending"}

        mock_result = MagicMock()
        mock_result.requests = [req]
        mock_result.found_count = 1
        mock_result.not_found_ids = []
        mock_result.total_requested = 1

        container, _ = _make_container_with_query_bus(result=mock_result)
        args = _make_ns(_container=container, request_ids=["r-1"])

        result = await handle_get_multiple_requests(args)

        req.model_dump.assert_called_once()
        assert result["requests"][0] == {"request_id": "r-1", "status": "pending"}

    @pytest.mark.asyncio
    async def test_result_not_found_ids_in_response(self):
        """not_found_ids list is included in the response."""
        from orb.interface.request_command_handlers import handle_get_multiple_requests

        mock_result = MagicMock()
        mock_result.requests = []
        mock_result.found_count = 0
        mock_result.not_found_ids = ["r-missing"]
        mock_result.total_requested = 1

        container, _ = _make_container_with_query_bus(result=mock_result)
        args = _make_ns(_container=container, request_ids=["r-missing"])

        result = await handle_get_multiple_requests(args)

        assert "r-missing" in result["not_found_ids"]

    @pytest.mark.asyncio
    async def test_combined_all_id_sources(self):
        """IDs from request_ids + flag_request_ids + flag_ids are all forwarded."""
        from orb.interface.request_command_handlers import handle_get_multiple_requests

        mock_result = MagicMock()
        mock_result.requests = []
        mock_result.found_count = 0
        mock_result.not_found_ids = []
        mock_result.total_requested = 3

        container, query_bus = _make_container_with_query_bus(result=mock_result)
        args = _make_ns(
            _container=container,
            request_ids=["r-1"],
            flag_request_ids=["r-2"],
            flag_ids=["r-3"],
        )

        await handle_get_multiple_requests(args)

        query_arg = query_bus.execute.call_args[0][0]
        assert set(query_arg.request_ids) == {"r-1", "r-2", "r-3"}
