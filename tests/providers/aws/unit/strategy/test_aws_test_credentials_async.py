"""Non-blocking contract for AWSProviderStrategy.test_credentials_async."""

import asyncio
import time
from unittest.mock import patch

import pytest

from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy


@pytest.mark.asyncio
async def test_test_credentials_async_offloads_blocking_call_to_a_thread() -> None:
    """The blocking boto3 credential discovery must not run on the event loop.

    ``AWSSessionFactory.discover_credentials`` is patched to a synchronous,
    time.sleep-based stand-in for a slow network call. A concurrent asyncio
    task is used as a canary: if the event loop were blocked by the
    credential call, the canary would make no progress until after the
    credential call returns. Progress during the call proves the work ran
    off the loop (via ``asyncio.to_thread``).
    """
    progressed_during_call = False

    def blocking_discover_credentials(credential_source, region):
        time.sleep(0.2)
        return {"success": True}

    async def canary() -> None:
        nonlocal progressed_during_call
        await asyncio.sleep(0.05)
        progressed_during_call = True

    with patch(
        "orb.providers.aws.session_factory.AWSSessionFactory.discover_credentials",
        side_effect=blocking_discover_credentials,
    ):
        canary_task = asyncio.create_task(canary())
        result = await AWSProviderStrategy.test_credentials_async(region="us-east-1")
        # Join the canary task; the return value (None) is intentionally discarded.
        _ = await canary_task

    assert result == {"success": True}
    assert progressed_during_call is True


@pytest.mark.asyncio
async def test_test_credentials_async_delegates_to_session_factory() -> None:
    """Delegates to AWSSessionFactory.discover_credentials with the given args."""
    with patch(
        "orb.providers.aws.session_factory.AWSSessionFactory.discover_credentials",
        return_value={"success": True},
    ) as mock_discover:
        result = await AWSProviderStrategy.test_credentials_async("my-profile", region="eu-west-1")

    mock_discover.assert_called_once_with("my-profile", "eu-west-1")
    assert result == {"success": True}
