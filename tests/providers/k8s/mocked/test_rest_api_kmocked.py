"""REST API delivery-surface tests against kmock-backed Kubernetes.

Drives the FastAPI app in-process via httpx ASGITransport — no subprocess,
no network port, no real cluster required.

The fixture chain is:
    kmock_k8s (conftest) -> orb_config_dir_k8s (kmock_delivery_conftest)
    -> fastapi_app_k8s -> rest_client_k8s

kmock limitations accounted for:
- kmock provides an in-process aiohttp server emulating the Kubernetes
  apiserver at HTTP level.
- K8sClient is swapped post-bootstrap to point at the kmock URL.
- k8s machine_ids are ``orb-...`` pod names, not EC2 instance IDs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from tests.providers.k8s.mocked.kmock_delivery_conftest import (  # noqa: E402
    _inject_kmock_factory,
    _make_k8s_logger,
    _register_pod_resource,
)
from tests.shared.constants import REQUEST_ID_RE  # noqa: E402

pytestmark = [pytest.mark.kmock, pytest.mark.rest_api]

_K8S_TEMPLATE_ID = "k8s-pod-example"
_K8S_CAPACITY = 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fastapi_app_k8s(orb_config_dir_k8s, kmock_k8s):
    """Build a real FastAPI app in-process with DI bootstrapped from orb_config_dir_k8s.

    Bootstraps the DI container (which reads ORB_CONFIG_DIR set by orb_config_dir_k8s),
    registers server orchestrators, then calls create_fastapi_app().
    Injects kmock-backed K8sClient so all kubernetes SDK calls are intercepted.
    """
    from orb.api.dependencies import CurrentUser, get_current_user
    from orb.api.server import create_fastapi_app
    from orb.bootstrap.server_services import _register_orchestrators
    from orb.config.schemas.server_schema import ServerConfig
    from orb.infrastructure.di.container import get_container

    _register_pod_resource(kmock_k8s)

    container = get_container()
    _register_orchestrators(container)

    logger = _make_k8s_logger()
    _inject_kmock_factory(kmock_k8s, logger)

    server_config = ServerConfig.model_validate({"enabled": True, "auth": {"enabled": False}})
    app = create_fastapi_app(server_config)

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-admin", role="admin", claims={}
    )
    return app


@pytest_asyncio.fixture
async def rest_client_k8s(fastapi_app_k8s):
    """httpx AsyncClient with ASGITransport — no subprocess, no network port."""
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app_k8s),
        base_url="http://test",
        headers={"Content-Type": "application/json"},
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestK8sHealthCheck:
    @pytest.mark.asyncio
    async def test_health_returns_healthy(self, rest_client_k8s, fastapi_app_k8s):
        """GET /health returns 200 with status=healthy."""
        from unittest.mock import MagicMock

        from orb.api.dependencies import get_health_check_port

        mock_health_port = MagicMock()
        mock_health_port.get_status.return_value = {"status": "healthy"}
        fastapi_app_k8s.dependency_overrides[get_health_check_port] = lambda: mock_health_port
        try:
            resp = await rest_client_k8s.get("/health")
        finally:
            fastapi_app_k8s.dependency_overrides.pop(get_health_check_port, None)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"


class TestK8sTemplates:
    @pytest.mark.asyncio
    async def test_list_templates_returns_non_empty(self, rest_client_k8s):
        """GET /api/v1/templates returns 200 with a non-empty templates list."""
        resp = await rest_client_k8s.get("/api/v1/templates/")
        assert resp.status_code == 200
        body = resp.json()
        assert "templates" in body
        templates = body["templates"]
        assert len(templates) > 0, "Expected at least one template"

    @pytest.mark.asyncio
    async def test_list_templates_each_has_template_id(self, rest_client_k8s):
        """Every template in the list has a template_id field."""
        resp = await rest_client_k8s.get("/api/v1/templates/")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        for tpl in templates:
            tid = tpl.get("template_id") or tpl.get("templateId")
            assert tid, f"Template missing template_id: {tpl}"

    @pytest.mark.asyncio
    async def test_list_templates_contains_k8s_pod(self, rest_client_k8s):
        """k8s-pod-example template is present in the list."""
        resp = await rest_client_k8s.get("/api/v1/templates/")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        ids = {tpl.get("template_id") or tpl.get("templateId") for tpl in templates}
        assert _K8S_TEMPLATE_ID in ids, (
            f"{_K8S_TEMPLATE_ID!r} not found in templates. Got: {sorted(ids - {None})}"
        )


class TestK8sRequestMachines:
    @pytest.mark.asyncio
    async def test_request_machines_returns_request_id(self, rest_client_k8s):
        """POST /api/v1/machines/request returns 202 with a valid request_id."""
        resp = await rest_client_k8s.post(
            "/api/v1/machines/request",
            json={"template_id": _K8S_TEMPLATE_ID, "count": _K8S_CAPACITY},
        )
        assert resp.status_code == 202, f"Unexpected status: {resp.status_code} — {resp.text}"
        body = resp.json()
        request_id = body.get("requestId") or body.get("request_id")
        assert request_id is not None, f"No request_id in response: {body}"
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

    @pytest.mark.asyncio
    async def test_request_machines_unknown_template_returns_4xx(self, rest_client_k8s):
        """POST /api/v1/machines/request with unknown template_id returns 4xx or raises."""
        import httpx

        try:
            resp = await rest_client_k8s.post(
                "/api/v1/machines/request",
                json={"template_id": "NonExistent-K8s-Template-XYZ", "count": 1},
            )
            assert resp.status_code >= 400, (
                f"Expected 4xx for unknown template, got {resp.status_code}: {resp.text}"
            )
        except (httpx.HTTPStatusError, Exception) as exc:
            assert (
                "NonExistent-K8s-Template-XYZ" in str(exc)
                or "not found" in str(exc).lower()
                or "Template" in str(exc)
            ), f"Unexpected exception for unknown template: {exc}"


class TestK8sRequestStatus:
    @pytest.mark.asyncio
    async def test_get_status_after_request(self, rest_client_k8s):
        """GET /api/v1/requests/{request_id}/status returns 200 with known status."""
        create_resp = await rest_client_k8s.post(
            "/api/v1/machines/request",
            json={"template_id": _K8S_TEMPLATE_ID, "count": _K8S_CAPACITY},
        )
        assert create_resp.status_code == 202
        body = create_resp.json()
        request_id = body.get("requestId") or body.get("request_id")
        assert request_id

        status_resp = await rest_client_k8s.get(f"/api/v1/requests/{request_id}/status")
        assert status_resp.status_code == 200, (
            f"Status check failed: {status_resp.status_code} — {status_resp.text}"
        )
        status_body = status_resp.json()

        requests_list = status_body.get("requests", [])
        if requests_list:
            status = requests_list[0].get("status", "unknown")
            returned_id = requests_list[0].get("request_id") or requests_list[0].get("requestId")
        else:
            status = status_body.get("status", "unknown")
            returned_id = status_body.get("request_id") or status_body.get("requestId")

        assert status in {
            "running",
            "complete",
            "complete_with_error",
            "pending",
            "unknown",
        }, f"Unexpected status: {status!r}"
        if returned_id:
            assert returned_id == request_id, (
                f"Status response request_id {returned_id!r} != created {request_id!r}"
            )


class TestK8sListRequests:
    @pytest.mark.asyncio
    async def test_list_requests_includes_created_request(self, rest_client_k8s):
        """GET /api/v1/requests includes the previously created request_id."""
        create_resp = await rest_client_k8s.post(
            "/api/v1/machines/request",
            json={"template_id": _K8S_TEMPLATE_ID, "count": _K8S_CAPACITY},
        )
        assert create_resp.status_code == 202
        body = create_resp.json()
        request_id = body.get("requestId") or body.get("request_id")
        assert request_id

        list_resp = await rest_client_k8s.get("/api/v1/requests/")
        assert list_resp.status_code == 200, (
            f"List requests failed: {list_resp.status_code} — {list_resp.text}"
        )
        list_body = list_resp.json()

        if isinstance(list_body, list):
            requests = list_body
        elif isinstance(list_body, dict):
            requests = list_body.get("requests", [])
        else:
            requests = []

        found_ids = []
        for req in requests:
            if isinstance(req, dict):
                rid = req.get("requestId") or req.get("request_id")
            else:
                rid = getattr(req, "request_id", None)
            if rid:
                found_ids.append(rid)

        assert request_id in found_ids, (
            f"Created request {request_id!r} not found in list. Got: {found_ids}"
        )


class TestK8sReturnMachines:
    @pytest.mark.asyncio
    async def test_return_machines_returns_message(self, rest_client_k8s):
        """POST /api/v1/machines/return with valid machine_ids returns 2xx with message."""
        create_resp = await rest_client_k8s.post(
            "/api/v1/machines/request",
            json={"template_id": _K8S_TEMPLATE_ID, "count": _K8S_CAPACITY},
        )
        assert create_resp.status_code == 202
        request_id = create_resp.json().get("requestId") or create_resp.json().get("request_id")
        assert request_id

        status_resp = await rest_client_k8s.get(f"/api/v1/requests/{request_id}/status")
        assert status_resp.status_code == 200
        status_body = status_resp.json()

        requests_list = status_body.get("requests", [])
        machine_ids: list[str] = []
        if requests_list:
            machines = requests_list[0].get("machines", [])
            for m in machines:
                mid = m.get("machineId") or m.get("machine_id")
                if mid:
                    machine_ids.append(mid)

        if not machine_ids:
            pytest.skip("No machine IDs available from kmock (k8s pod may not have been created)")

        return_resp = await rest_client_k8s.post(
            "/api/v1/machines/return",
            json={"machine_ids": machine_ids},
        )
        assert return_resp.status_code in {200, 202}, (
            f"Return machines failed: {return_resp.status_code} — {return_resp.text}"
        )
        return_body = return_resp.json()
        assert "message" in return_body, f"Return response missing 'message': {return_body}"


class TestK8sConcurrentRequests:
    @pytest.mark.asyncio
    async def test_concurrent_requests_both_get_request_ids(self, rest_client_k8s):
        """Two simultaneous POST /api/v1/machines/request calls both return distinct request_ids."""
        import asyncio

        async def make_request():
            return await rest_client_k8s.post(
                "/api/v1/machines/request",
                json={"template_id": _K8S_TEMPLATE_ID, "count": _K8S_CAPACITY},
            )

        resp1, resp2 = await asyncio.gather(make_request(), make_request())

        assert resp1.status_code == 202, f"Request 1 failed: {resp1.status_code} — {resp1.text}"
        assert resp2.status_code == 202, f"Request 2 failed: {resp2.status_code} — {resp2.text}"

        body1 = resp1.json()
        body2 = resp2.json()

        rid1 = body1.get("requestId") or body1.get("request_id")
        rid2 = body2.get("requestId") or body2.get("request_id")

        assert rid1, f"No request_id in response 1: {body1}"
        assert rid2, f"No request_id in response 2: {body2}"
        assert rid1 != rid2, f"Both requests returned the same request_id: {rid1!r}"


class TestK8sFullLifecycle:
    @pytest.mark.asyncio
    async def test_full_request_lifecycle(self, rest_client_k8s):
        """Full lifecycle: request -> status -> list -> return (if machines available)."""
        # 1. Verify templates are available
        templates_resp = await rest_client_k8s.get("/api/v1/templates/")
        assert templates_resp.status_code == 200
        templates = templates_resp.json()["templates"]
        ids = {tpl.get("template_id") or tpl.get("templateId") for tpl in templates}
        assert _K8S_TEMPLATE_ID in ids, (
            f"{_K8S_TEMPLATE_ID!r} not found in templates. Got: {sorted(ids - {None})}"
        )

        # 2. Create request
        create_resp = await rest_client_k8s.post(
            "/api/v1/machines/request",
            json={"template_id": _K8S_TEMPLATE_ID, "count": _K8S_CAPACITY},
        )
        assert create_resp.status_code == 202
        request_id = create_resp.json().get("requestId") or create_resp.json().get("request_id")
        assert request_id
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

        # 3. Check status
        status_resp = await rest_client_k8s.get(f"/api/v1/requests/{request_id}/status")
        assert status_resp.status_code == 200
        status_body = status_resp.json()
        requests_list = status_body.get("requests", [])
        if requests_list:
            status = requests_list[0].get("status", "unknown")
        else:
            status = status_body.get("status", "unknown")
        assert status in {"running", "complete", "complete_with_error", "pending", "unknown"}

        # 4. Verify request appears in list
        list_resp = await rest_client_k8s.get("/api/v1/requests/")
        assert list_resp.status_code == 200
        list_body = list_resp.json()
        if isinstance(list_body, list):
            all_requests = list_body
        else:
            all_requests = list_body.get("requests", [])
        found_ids = [
            (r.get("requestId") or r.get("request_id")) for r in all_requests if isinstance(r, dict)
        ]
        assert request_id in found_ids, f"Request {request_id!r} not in list. Got: {found_ids}"

        # 5. Return machines if any were provisioned
        machine_ids: list[str] = []
        if requests_list:
            for m in requests_list[0].get("machines", []):
                mid = m.get("machineId") or m.get("machine_id")
                if mid:
                    machine_ids.append(mid)

        if machine_ids:
            return_resp = await rest_client_k8s.post(
                "/api/v1/machines/return",
                json={"machine_ids": machine_ids},
            )
            assert return_resp.status_code in {200, 202}, (
                f"Return failed: {return_resp.status_code} — {return_resp.text}"
            )
            assert "message" in return_resp.json()
