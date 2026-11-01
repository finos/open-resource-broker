"""REST delivery for every Kubernetes workload kind.

The live tier only exercises Pods.  Here each kind (Pod, Deployment,
StatefulSet, Job) is acquired through the real DI container and REST app with
the Kubernetes API emulated by kmock, then read back through the status and
list routes.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from tests.providers.k8s.mocked.kmock_delivery_conftest import (
    _inject_kmock_factory,
    _make_k8s_logger,
    _register_deployment_resource,
    _register_job_resource,
    _register_pod_resource,
    _register_statefulset_resource,
)
from tests.shared.constants import REQUEST_ID_RE

# Wire values external HostFactory consumers accept, taken from the committed
# baseline so a new value on the k8s path has to be a deliberate contract change.
_BASELINE = json.loads(
    (Path(__file__).resolve().parents[3] / "fixtures" / "wire_format_baseline.json").read_text()
)
_WIRE_REQUEST_STATUSES = {v for k, v in _BASELINE.items() if k.startswith("request_status:")}
_WIRE_MACHINE_RESULTS = {v for k, v in _BASELINE.items() if k.startswith("machine_result:")}

pytestmark = [pytest.mark.kmock, pytest.mark.rest_api]

PLURALS = {"Pod": "pods", "Deployment": "deployments", "StatefulSet": "statefulsets", "Job": "jobs"}

KINDS = {
    "Pod": "k8s-pod-example",
    "Deployment": "k8s-deployment-example",
    "StatefulSet": "k8s-statefulset-example",
    "Job": "k8s-job-example",
}


@pytest_asyncio.fixture
async def client(orb_config_dir_k8s, kmock_k8s):
    from orb.api.dependencies import CurrentUser, get_current_user
    from orb.api.server import create_fastapi_app
    from orb.bootstrap.server_services import _register_orchestrators
    from orb.config.schemas.server_schema import ServerConfig
    from orb.infrastructure.di.container import get_container

    for register in (
        _register_pod_resource,
        _register_deployment_resource,
        _register_statefulset_resource,
        _register_job_resource,
    ):
        register(kmock_k8s)

    _register_orchestrators(get_container())
    _inject_kmock_factory(kmock_k8s, _make_k8s_logger())

    app = create_fastapi_app(
        ServerConfig.model_validate({"enabled": True, "auth": {"enabled": False}})
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-admin", role="admin", claims={}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(KINDS))
async def test_template_for_kind_is_listed_with_its_provider_api(client, kind) -> None:
    resp = await client.get("/api/v1/templates/")
    assert resp.status_code == 200, resp.text
    by_id = {(t.get("template_id") or t.get("templateId")): t for t in resp.json()["templates"]}
    template = by_id[KINDS[kind]]
    assert (template.get("provider_api") or template.get("providerApi")) == kind


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(KINDS))
async def test_acquire_status_and_list_for_kind(client, kmock_k8s, kind) -> None:
    created = await client.post(
        "/api/v1/machines/request", json={"template_id": KINDS[kind], "count": 1}
    )
    assert created.status_code == 202, created.text
    body = created.json()
    request_id = body.get("requestId") or body.get("request_id")
    assert request_id and REQUEST_ID_RE.match(request_id), body

    created_kinds = {key[0].plural for key in kmock_k8s.objects}
    assert PLURALS[kind] in created_kinds, created_kinds

    status = await client.get(f"/api/v1/requests/{request_id}/status")
    assert status.status_code == 200, status.text
    entry = status.json()["requests"][0]
    assert (entry.get("requestId") or entry.get("request_id")) == request_id
    assert entry["status"] in _WIRE_REQUEST_STATUSES
    assert entry["machines"], entry
    for machine in entry["machines"]:
        assert machine["machineId"] and machine["name"]
        assert machine["result"] in _WIRE_MACHINE_RESULTS

    listed = await client.get("/api/v1/requests/")
    assert listed.status_code == 200, listed.text
    rows = listed.json()["requests"]
    assert request_id in {r.get("requestId") or r.get("request_id") for r in rows}
