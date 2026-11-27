"""Multi-worker warnings emitted while ``create_fastapi_app`` builds the app."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest

from orb.api.server import create_fastapi_app
from orb.config.schemas.server_schema import AuthConfig, RateLimitConfig, ServerConfig

pytestmark = [pytest.mark.unit, pytest.mark.api]


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "orb.api.server"]


def _register_events_route(app: Any) -> None:
    # The SSE warning looks for an /events path among the app's routes. A plain
    # route keeps that path visible, whichever way the framework wraps routers.
    async def events() -> dict[str, str]:
        return {}

    app.add_api_route("/api/v1/events", events)


def _build(workers: int) -> None:
    with patch("orb.api.server._register_routers", side_effect=_register_events_route):
        create_fastapi_app(
            ServerConfig(  # type: ignore[call-arg]
                enabled=True,
                workers=workers,
                auth=AuthConfig(enabled=False, strategy="replace"),  # type: ignore[call-arg]
                rate_limiting=RateLimitConfig(enabled=True, requests_per_minute=60, burst=10),  # type: ignore[call-arg]
            )
        )


def test_sse_warning_is_emitted_for_several_workers(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="orb.api.server"):
        _build(workers=2)

    messages = _messages(caplog)
    assert any(m.startswith("MULTI_WORKER_SSE:") for m in messages)
    # The warnings raised while installing middleware are separate and must not
    # be mistaken for the SSE one.
    assert any(m.startswith("MULTI_WORKER_FLEET_RELEASE:") for m in messages)
    assert any(m.startswith("MULTI_WORKER_RATE_LIMIT:") for m in messages)


def test_no_multi_worker_warning_for_a_single_worker(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="orb.api.server"):
        _build(workers=1)

    assert not any(m.startswith("MULTI_WORKER") for m in _messages(caplog))
