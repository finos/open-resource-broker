"""The CLI delivers in-flight domain events when a command ends, success or failure."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.cli import main as cli_main


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drain_events_awaits_the_publisher():
    publisher = MagicMock()
    publisher.drain = AsyncMock()
    container = MagicMock()
    container.get_optional.return_value = publisher
    with patch.object(cli_main, "get_container", return_value=container):
        await cli_main._drain_events()
    publisher.drain.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drain_events_never_raises():
    with patch.object(cli_main, "get_container", side_effect=RuntimeError("no container")):
        await cli_main._drain_events()


@pytest.mark.unit
def test_drain_runs_in_the_finally_block_of_command_execution():
    source = inspect.getsource(cli_main.main)
    finally_block = source.split("finally:\n            await _drain_events()")
    assert len(finally_block) == 2, "drain must be the first statement of the command's finally"
