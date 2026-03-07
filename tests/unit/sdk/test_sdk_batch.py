"""Unit tests for ORBClient.batch()."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.exceptions import SDKError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _initialized_sdk() -> ORBClient:
    sdk = ORBClient(config={"provider": "aws"})
    sdk._initialized = True
    sdk._query_bus = AsyncMock()
    sdk._command_bus = AsyncMock()
    return sdk


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBatch:
    @pytest.mark.asyncio
    async def test_batch_raises_when_not_initialized(self):
        sdk = ORBClient(config={"provider": "aws"})
        with pytest.raises(SDKError, match="not initialized"):
            await sdk.batch([AsyncMock()()])

    @pytest.mark.asyncio
    async def test_batch_empty_list_returns_empty(self):
        sdk = _initialized_sdk()
        result = await sdk.batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_successful_operations(self):
        sdk = _initialized_sdk()

        async def op(val):
            return val

        results = await sdk.batch([op(1), op(2), op(3)])
        assert results == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_batch_preserves_order(self):
        sdk = _initialized_sdk()

        async def slow(val):
            await asyncio.sleep(0)
            return val

        results = await sdk.batch([slow("a"), slow("b"), slow("c")])
        assert results == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_batch_mixed_success_and_failure(self):
        sdk = _initialized_sdk()

        async def ok():
            return "ok"

        async def fail():
            raise ValueError("boom")

        results = await sdk.batch([ok(), fail(), ok()])
        assert results[0] == "ok"
        assert isinstance(results[1], ValueError)
        assert str(results[1]) == "boom"
        assert results[2] == "ok"

    @pytest.mark.asyncio
    async def test_batch_all_failures_captured(self):
        sdk = _initialized_sdk()

        async def fail(msg):
            raise RuntimeError(msg)

        results = await sdk.batch([fail("e1"), fail("e2")])
        assert all(isinstance(r, RuntimeError) for r in results)
        assert str(results[0]) == "e1"
        assert str(results[1]) == "e2"

    @pytest.mark.asyncio
    async def test_batch_returns_list(self):
        sdk = _initialized_sdk()

        async def op():
            return {"created_request_id": "req-1"}

        results = await sdk.batch([op()])
        assert isinstance(results, list)
        assert results[0] == {"created_request_id": "req-1"}
