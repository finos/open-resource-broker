"""Tests for ORBClient.wait_for_request() and wait_for_return() helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.exceptions import RequestTimeoutError, SDKError


def _make_client(initialized: bool = True) -> ORBClient:
    """Create a minimal ORBClient with _initialized set directly."""
    client = object.__new__(ORBClient)
    # Minimal state needed by the wait methods
    client._initialized = initialized  # type: ignore[attr-defined]
    return client


class TestWaitForRequestTerminalImmediately:
    """wait_for_request returns immediately when status is already terminal."""

    @pytest.mark.asyncio
    async def test_wait_for_request_returns_immediately_if_terminal(self) -> None:
        client = _make_client()
        request_entry = {"status": "complete", "request_id": "req-1"}
        client.get_request_status = AsyncMock(  # type: ignore[attr-defined]
            return_value={"requests": [request_entry]}
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            returned = await client.wait_for_request("req-1")

        assert returned == request_entry
        mock_sleep.assert_not_called()
        client.get_request_status.assert_called_once()  # type: ignore[attr-defined]


class TestWaitForRequestPolls:
    """wait_for_request polls until terminal status is reached."""

    @pytest.mark.asyncio
    async def test_wait_for_request_polls_until_terminal(self) -> None:
        client = _make_client()
        pending_entry = {"status": "pending"}
        completed_entry = {"status": "complete"}
        client.get_request_status = AsyncMock(  # type: ignore[attr-defined]
            side_effect=[
                {"requests": [pending_entry]},
                {"requests": [pending_entry]},
                {"requests": [completed_entry]},
            ]
        )

        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            result = await client.wait_for_request("req-2", poll_interval=5.0, timeout=300.0)

        assert result == completed_entry
        assert len(sleep_calls) == 2
        assert client.get_request_status.call_count == 3  # type: ignore[attr-defined]


class TestWaitForRequestTimeout:
    """wait_for_request raises TimeoutError when timeout expires."""

    @pytest.mark.asyncio
    async def test_wait_for_request_raises_timeout_error(self) -> None:
        client = _make_client()
        client.get_request_status = AsyncMock(  # type: ignore[attr-defined]
            return_value={"requests": [{"status": "pending"}]}
        )

        # Use a very short timeout so the loop exits quickly
        with patch("asyncio.sleep", new_callable=AsyncMock):
            # Patch get_running_loop().time() to advance past deadline after first poll
            import asyncio

            loop = asyncio.get_event_loop()
            call_count = 0

            def advancing_time() -> float:
                nonlocal call_count
                call_count += 1
                # First two calls return 0 (deadline setup + first check),
                # subsequent calls return a value past the deadline
                if call_count <= 2:
                    return 0.0
                return 999.0

            with patch.object(loop, "time", side_effect=advancing_time):
                with pytest.raises(RequestTimeoutError) as exc_info:
                    await client.wait_for_request("req-3", timeout=1.0, poll_interval=0.1)

        assert "req-3" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_wait_for_request_timeout_zero_raises_if_not_terminal(self) -> None:
        client = _make_client()
        client.get_request_status = AsyncMock(  # type: ignore[attr-defined]
            return_value={"requests": [{"status": "in_progress"}]}
        )

        import asyncio

        loop = asyncio.get_event_loop()

        # With timeout=0, deadline == start time, so remaining <= 0 after first poll
        start = loop.time()
        call_count = 0

        def time_at_deadline() -> float:
            nonlocal call_count
            call_count += 1
            # First call (deadline = start + 0), second call returns same → remaining = 0
            return start

        with patch.object(loop, "time", side_effect=time_at_deadline):
            with pytest.raises(RequestTimeoutError):
                await client.wait_for_request("req-4", timeout=0.0, poll_interval=1.0)


class TestWaitForReturnDelegates:
    """wait_for_return delegates to wait_for_request."""

    @pytest.mark.asyncio
    async def test_wait_for_return_delegates_to_wait_for_request(self) -> None:
        client = _make_client()
        expected = {"status": "complete", "request_id": "ret-1"}

        with patch.object(
            client,
            "wait_for_request",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_wait:
            result = await client.wait_for_return("ret-1", timeout=120.0, poll_interval=5.0)

        assert result == expected
        mock_wait.assert_called_once_with("ret-1", timeout=120.0, poll_interval=5.0)


class TestWaitForRequestNotInitialized:
    """wait_for_request raises SDKError when client is not initialized."""

    @pytest.mark.asyncio
    async def test_wait_for_request_raises_sdk_error_if_not_initialized(self) -> None:
        client = _make_client(initialized=False)

        with pytest.raises(SDKError, match="not initialized"):
            await client.wait_for_request("req-5")
