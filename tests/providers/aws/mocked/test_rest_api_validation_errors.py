"""A template the provider rejects is a client error, not a server error."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.providers.aws.mocked.test_rest_api_onmoto import fastapi_app  # noqa: F401

pytestmark = [pytest.mark.moto, pytest.mark.rest_api]


@pytest.mark.asyncio
async def test_template_the_provider_does_not_support_is_a_client_error(
    fastapi_app,  # noqa: F811
    orb_config_dir,
):
    # The MicroVM handler does not offer spot capacity, so a spot template that
    # selects it is rejected by template validation.
    templates_file = orb_config_dir / "aws_templates.json"
    data = json.loads(templates_file.read_text())
    unsupported = dict(next(t for t in data["templates"] if t["templateId"] == "RunInstances-Spot"))
    unsupported["templateId"] = "MicroVM-Spot"
    unsupported["providerApi"] = "MicroVM"
    data["templates"].append(unsupported)
    templates_file.write_text(json.dumps(data))

    transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        refreshed = await client.post("/api/v1/templates/refresh")
        assert refreshed.status_code == 200, refreshed.text
        response = await client.post(
            "/api/v1/machines/request",
            json={"template_id": "MicroVM-Spot", "count": 1},
        )

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"]


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, -1])
async def test_non_positive_machine_count_is_a_client_error(
    fastapi_app,  # noqa: F811
    count,
):
    transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/machines/request",
            json={"template_id": "RunInstances-OnDemand", "count": count},
        )

    assert response.status_code == 400, response.text
    assert response.json()["success"] is False


@pytest.mark.asyncio
async def test_return_without_targets_is_a_client_error(
    fastapi_app,  # noqa: F811
):
    transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/machines/return", json={"machine_ids": []})

    assert response.status_code == 400, response.text
    assert response.json()["success"] is False


@pytest.mark.asyncio
async def test_batch_status_without_request_ids_is_a_client_error(
    fastapi_app,  # noqa: F811
):
    transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/requests/status", json={"request_ids": []})

    assert response.status_code == 400, response.text
    assert response.json()["success"] is False
