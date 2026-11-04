"""SDK cleanup delivers in-flight domain events before tearing the application down."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.domain.base.ports import EventPublisherPort
from orb.sdk.client import ORBClient


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_drains_events_before_application_cleanup():
    order: list[str] = []
    publisher = MagicMock()
    publisher.drain = AsyncMock(side_effect=lambda: order.append("drain"))
    container = MagicMock()
    container.get_optional.side_effect = lambda t: publisher if t is EventPublisherPort else None
    app = MagicMock()
    app.cleanup = AsyncMock(side_effect=lambda: order.append("app_cleanup"))

    client = object.__new__(ORBClient)
    client._container = container
    client._app = app
    client._initialized = True
    client._methods = {}
    client._discovery = None

    await client.cleanup()

    assert order == ["drain", "app_cleanup"]
