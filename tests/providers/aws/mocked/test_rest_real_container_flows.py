"""REST flows against a real DI container, real registry and real JSON storage.

Only the AWS API itself is replaced (moto).  The orchestrators, command and
query buses, provider registry, strategy resolution and the JSON storage
layer are all production code, so a mis-wired orchestrator fails here rather
than being masked by a mocked dependency override.

Flows covered: acquire -> status -> return, cancel, list return requests, and a
restart (fresh container, same storage directory) that re-reads prior state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.providers.aws.mocked.conftest import (
    _inject_moto_factory,
    _make_logger,
    _make_moto_aws_client,
)

pytestmark = [pytest.mark.moto, pytest.mark.rest_api, pytest.mark.e2e]

TEMPLATE_ID = "RunInstances-OnDemand"


def _build_app() -> Any:
    """Bootstrap the container from ORB_CONFIG_DIR and build the REST app."""
    from orb.api.dependencies import CurrentUser, get_current_user
    from orb.api.server import create_fastapi_app
    from orb.bootstrap.server_services import _register_orchestrators
    from orb.config.schemas.server_schema import ServerConfig
    from orb.infrastructure.di.container import get_container

    container = get_container()
    _register_orchestrators(container)
    _inject_moto_factory(_make_moto_aws_client(), _make_logger(), None)

    app = create_fastapi_app(
        ServerConfig.model_validate({"enabled": True, "auth": {"enabled": False}})
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-admin", role="admin", claims={}
    )
    return app


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


def _id(body: dict[str, Any]) -> str:
    value = body.get("requestId") or body.get("request_id")
    assert value, f"no request id in {body}"
    return value


def _request_entry(status_body: dict[str, Any]) -> dict[str, Any]:
    entries = status_body.get("requests")
    assert entries, f"status response carries no request entry: {status_body}"
    return entries[0]


def _machine_ids(entry: dict[str, Any]) -> list[str]:
    return [m.get("machineId") or m.get("machine_id") for m in entry.get("machines", [])]


async def _acquire(client: httpx.AsyncClient, count: int = 1) -> str:
    resp = await client.post(
        "/api/v1/machines/request", json={"template_id": TEMPLATE_ID, "count": count}
    )
    assert resp.status_code == 202, resp.text
    return _id(resp.json())


@pytest.mark.asyncio
async def test_acquire_status_return(orb_config_dir) -> None:
    async with _client(_build_app()) as client:
        request_id = await _acquire(client)

        status = await client.get(f"/api/v1/requests/{request_id}/status")
        assert status.status_code == 200, status.text
        entry = _request_entry(status.json())
        assert (entry.get("requestId") or entry.get("request_id")) == request_id
        machines = _machine_ids(entry)
        assert len(machines) == 1 and all(machines), entry

        returned = await client.post("/api/v1/machines/return", json={"machine_ids": machines})
        assert returned.status_code in {200, 202}, returned.text
        return_id = _id(returned.json())
        assert return_id != request_id

        listed = await client.get("/api/v1/requests/return")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        rows = body if isinstance(body, list) else body.get("requests", [])
        assert return_id in {r.get("requestId") or r.get("request_id") for r in rows}, rows


def _instance_states(ec2_client: Any, instance_ids: list[str]) -> set[str]:
    reservations = ec2_client.describe_instances(InstanceIds=instance_ids)["Reservations"]
    return {i["State"]["Name"] for r in reservations for i in r["Instances"]}


@pytest.mark.asyncio
async def test_cancel_request_terminates_its_machines(orb_config_dir, ec2_client) -> None:
    async with _client(_build_app()) as client:
        request_id = await _acquire(client)
        entry = _request_entry((await client.get(f"/api/v1/requests/{request_id}/status")).json())
        machines = _machine_ids(entry)
        assert machines and _instance_states(ec2_client, machines) == {"running"}

        cancelled = await client.delete(f"/api/v1/requests/{request_id}")
        assert cancelled.status_code == 200, cancelled.text
        assert _id(cancelled.json()) == request_id

    assert _instance_states(ec2_client, machines) <= {"shutting-down", "terminated"}


@pytest.mark.asyncio
async def test_cancel_unknown_request_is_not_found(orb_config_dir) -> None:
    async with _client(_build_app()) as client:
        resp = await client.delete("/api/v1/requests/req-00000000-0000-0000-0000-00000000dead")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_state_survives_restart(orb_config_dir, tmp_path: Path) -> None:
    from orb.infrastructure.di.container import reset_container
    from tests.utilities.reset_singletons import reset_all_singletons

    async with _client(_build_app()) as client:
        request_id = await _acquire(client)
        before = _request_entry((await client.get(f"/api/v1/requests/{request_id}/status")).json())

    stored = json.loads((tmp_path / "data" / "request_database.json").read_text())
    assert request_id in json.dumps(stored)

    reset_container()
    reset_all_singletons()

    async with _client(_build_app()) as client:
        resp = await client.get(f"/api/v1/requests/{request_id}/status")
        assert resp.status_code == 200, resp.text
        after = _request_entry(resp.json())

        listed = (await client.get("/api/v1/requests/")).json()

    assert (after.get("requestId") or after.get("request_id")) == request_id
    assert after["status"] == before["status"]
    assert _machine_ids(after) == _machine_ids(before)
    rows = listed if isinstance(listed, list) else listed.get("requests", [])
    assert request_id in {r.get("requestId") or r.get("request_id") for r in rows}
