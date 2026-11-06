"""A template the provider rejects is a client error, not a server error."""

from __future__ import annotations

import httpx
import pytest

from tests.providers.aws.mocked.test_rest_api_onmoto import fastapi_app  # noqa: F401

pytestmark = [pytest.mark.moto, pytest.mark.rest_api]


@pytest.mark.asyncio
async def test_template_the_provider_does_not_support_is_a_client_error(fastapi_app):  # noqa: F811
    transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/machines/request",
            json={"template_id": "RunInstances-Spot", "count": 1},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"]
