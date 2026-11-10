"""GET /providers/ against the real container."""

from __future__ import annotations

import httpx
import pytest

from tests.providers.aws.mocked.test_rest_api_onmoto import fastapi_app  # noqa: F401

pytestmark = [pytest.mark.moto, pytest.mark.rest_api]


@pytest.mark.asyncio
async def test_list_providers_returns_the_configured_provider(fastapi_app):  # noqa: F811
    transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/providers/")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == body["total_count"] == 1
    assert body["providers"][0]["type"] == "aws"
    assert body["providers"][0]["enabled"] is True
    assert response.headers["x-orb-scheduler"]
