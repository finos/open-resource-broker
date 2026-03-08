"""REST API integration tests against moto-mocked AWS.

Drives the FastAPI app in-process via httpx ASGITransport — no subprocess,
no network port, no real AWS credentials required.

The fixture chain is:
    moto_aws (conftest) -> orb_config_dir (conftest) -> fastapi_app -> rest_client

Moto limitations accounted for (same patches as test_sdk_onmoto.py):
- SSM parameter resolution: patched out (moto cannot resolve SSM paths)
- AWSProvisioningAdapter: patched to synthesise instances from instance_ids
"""

import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.onmoto.conftest import _inject_moto_factory, _make_logger, _make_moto_aws_client
from tests.shared.scenarios import TestScenario, get_smoke_scenarios

from tests.shared.constants import REQUEST_ID_RE

REGION = "eu-west-2"

pytestmark = [pytest.mark.moto, pytest.mark.rest_api]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fastapi_app(orb_config_dir, moto_aws):
    """Build a real FastAPI app in-process with DI bootstrapped from orb_config_dir.

    Bootstraps the DI container (which reads ORB_CONFIG_DIR set by orb_config_dir),
    registers server services with server.enabled=True, then calls create_fastapi_app().
    Injects moto-backed AWS factory so all boto3 calls are intercepted by moto.
    """
    from orb.api.server import create_fastapi_app
    from orb.config.schemas.server_schema import ServerConfig
    from orb.infrastructure.di.container import get_container
    from orb.infrastructure.di.server_services import _register_api_handlers

    # Bootstrap DI (reads ORB_CONFIG_DIR from env, set by orb_config_dir fixture)
    container = get_container()

    # Register API handlers (server_services skips them when server.enabled=False by default)
    _register_api_handlers(container)

    # Re-register RequestMachinesRESTHandler with metrics=None to avoid calling
    # record_api_success/record_api_failure which don't exist on MetricsCollector.
    from orb.api.handlers.request_machines_handler import RequestMachinesRESTHandler
    from orb.domain.base.ports import ErrorHandlingPort, LoggingPort
    from orb.infrastructure.di.buses import CommandBus, QueryBus

    container.unregister(RequestMachinesRESTHandler)
    container.register_singleton(
        RequestMachinesRESTHandler,
        lambda c: RequestMachinesRESTHandler(
            query_bus=c.get(QueryBus),
            command_bus=c.get(CommandBus),
            logger=c.get(LoggingPort),
            error_handler=(
                c.get(ErrorHandlingPort) if c.is_registered(ErrorHandlingPort) else None
            ),
            metrics=None,
        ),
    )

    # Inject moto-backed AWS factory
    aws_client = _make_moto_aws_client()
    logger = _make_logger()
    _inject_moto_factory(aws_client, logger, None)

    # Build the FastAPI app with auth disabled
    server_config = ServerConfig.model_validate({"enabled": True, "auth": {"enabled": False}})
    app = create_fastapi_app(server_config)
    return app


@pytest_asyncio.fixture
async def rest_client(fastapi_app):
    """httpx AsyncClient with ASGITransport — no subprocess, no network port."""
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url="http://test",
        headers={"Content-Type": "application/json"},
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_returns_healthy(self, rest_client):
        """GET /health returns 200 with status=healthy."""
        resp = await rest_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"


class TestTemplates:
    @pytest.mark.asyncio
    async def test_list_templates_returns_non_empty(self, rest_client):
        """GET /api/v1/templates returns 200 with a non-empty templates list."""
        resp = await rest_client.get("/api/v1/templates/")
        assert resp.status_code == 200
        body = resp.json()
        assert "templates" in body
        templates = body["templates"]
        assert len(templates) > 0, "Expected at least one template"

    @pytest.mark.asyncio
    async def test_list_templates_each_has_template_id(self, rest_client):
        """Every template in the list has a template_id field."""
        resp = await rest_client.get("/api/v1/templates/")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        for tpl in templates:
            tid = tpl.get("template_id") or tpl.get("templateId")
            assert tid, f"Template missing template_id: {tpl}"

    @pytest.mark.asyncio
    async def test_list_templates_contains_run_instances(self, rest_client):
        """RunInstances-OnDemand template is present in the list."""
        resp = await rest_client.get("/api/v1/templates/")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        ids = {tpl.get("template_id") or tpl.get("templateId") for tpl in templates}
        assert "RunInstances-OnDemand" in ids, (
            f"'RunInstances-OnDemand' not found in templates. Got: {sorted(ids - {None})}"
        )


class TestRequestMachines:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_request_machines_returns_request_id(self, rest_client, scenario: TestScenario):
        """POST /api/v1/machines/request returns 202 with a valid request_id."""
        resp = await rest_client.post(
            "/api/v1/machines/request",
            json={"template_id": scenario.template_id, "count": scenario.capacity},
        )
        assert resp.status_code == 202, f"Unexpected status: {resp.status_code} — {resp.text}"
        body = resp.json()
        request_id = body.get("requestId") or body.get("request_id")
        assert request_id is not None, f"No request_id in response: {body}"
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

    @pytest.mark.asyncio
    async def test_request_machines_unknown_template_returns_4xx(self, rest_client):
        """POST /api/v1/machines/request with unknown template_id returns 4xx or raises."""
        import httpx

        try:
            resp = await rest_client.post(
                "/api/v1/machines/request",
                json={"template_id": "NonExistent-Template-XYZ", "count": 1},
            )
            # If we get a response, it must be an error status
            assert resp.status_code >= 400, (
                f"Expected 4xx for unknown template, got {resp.status_code}: {resp.text}"
            )
        except (httpx.HTTPStatusError, Exception) as exc:
            # An unhandled exception propagating out of the ASGI app also counts as
            # the server rejecting the request — verify it's template-related.
            assert "NonExistent-Template-XYZ" in str(exc) or "not found" in str(exc).lower() or "Template" in str(exc), (
                f"Unexpected exception for unknown template: {exc}"
            )


class TestRequestStatus:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_get_status_after_request(self, rest_client, scenario: TestScenario):
        """GET /api/v1/requests/{request_id}/status returns 200 with known status."""
        # Create a request first
        create_resp = await rest_client.post(
            "/api/v1/machines/request",
            json={"template_id": scenario.template_id, "count": scenario.capacity},
        )
        assert create_resp.status_code == 202
        body = create_resp.json()
        request_id = body.get("requestId") or body.get("request_id")
        assert request_id

        # Query status
        status_resp = await rest_client.get(f"/api/v1/requests/{request_id}/status")
        assert status_resp.status_code == 200, (
            f"Status check failed: {status_resp.status_code} — {status_resp.text}"
        )
        status_body = status_resp.json()

        # Extract status from response (may be nested under requests[0])
        requests_list = status_body.get("requests", [])
        if requests_list:
            status = requests_list[0].get("status", "unknown")
            returned_id = requests_list[0].get("request_id") or requests_list[0].get("requestId")
        else:
            status = status_body.get("status", "unknown")
            returned_id = status_body.get("request_id") or status_body.get("requestId")

        assert status in {"running", "complete", "complete_with_error", "pending", "unknown"}, (
            f"Unexpected status: {status!r}"
        )
        if returned_id:
            assert returned_id == request_id, (
                f"Status response request_id {returned_id!r} != created {request_id!r}"
            )


class TestListRequests:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_list_requests_includes_created_request(self, rest_client, scenario: TestScenario):
        """GET /api/v1/requests includes the previously created request_id."""
        # Create a request
        create_resp = await rest_client.post(
            "/api/v1/machines/request",
            json={"template_id": scenario.template_id, "count": scenario.capacity},
        )
        assert create_resp.status_code == 202
        body = create_resp.json()
        request_id = body.get("requestId") or body.get("request_id")
        assert request_id

        # List requests
        list_resp = await rest_client.get("/api/v1/requests/")
        assert list_resp.status_code == 200, (
            f"List requests failed: {list_resp.status_code} — {list_resp.text}"
        )
        list_body = list_resp.json()

        # Normalise to a flat list
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


class TestReturnMachines:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_return_machines_returns_message(self, rest_client, scenario: TestScenario):
        """POST /api/v1/machines/return with valid machine_ids returns 2xx with message."""
        # Create a request and get machine IDs from status
        create_resp = await rest_client.post(
            "/api/v1/machines/request",
            json={"template_id": scenario.template_id, "count": scenario.capacity},
        )
        assert create_resp.status_code == 202
        request_id = (
            create_resp.json().get("requestId") or create_resp.json().get("request_id")
        )
        assert request_id

        # Get status to find machine IDs
        status_resp = await rest_client.get(f"/api/v1/requests/{request_id}/status")
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
            pytest.skip("No machine IDs available from moto (RunInstances may not have fulfilled)")

        # Return the machines
        return_resp = await rest_client.post(
            "/api/v1/machines/return",
            json={"machine_ids": machine_ids},
        )
        assert return_resp.status_code in {200, 202}, (
            f"Return machines failed: {return_resp.status_code} — {return_resp.text}"
        )
        return_body = return_resp.json()
        # Response must carry a message field
        assert "message" in return_body, (
            f"Return response missing 'message': {return_body}"
        )

        # Poll for return completion
        import time

        return_request_id = return_body.get("request_id") or return_body.get("requestId")
        if return_request_id:
            deadline = time.time() + 10
            while time.time() < deadline:
                list_resp = await rest_client.get("/api/v1/requests/return")
                if list_resp.status_code == 200:
                    body = list_resp.json()
                    requests = body if isinstance(body, list) else body.get("requests", [])
                    done = any(
                        (r.get("request_id") or r.get("requestId")) == return_request_id
                        and r.get("status") == "complete"
                        for r in requests
                        if isinstance(r, dict)
                    )
                    if done:
                        break
                import asyncio
                await asyncio.sleep(0.5)


class TestFullLifecycle:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_full_request_lifecycle(self, rest_client, scenario: TestScenario):
        """Full lifecycle: request -> status -> list -> return (if machines available)."""
        # 1. Verify templates are available
        templates_resp = await rest_client.get("/api/v1/templates/")
        assert templates_resp.status_code == 200
        templates = templates_resp.json()["templates"]
        ids = {tpl.get("template_id") or tpl.get("templateId") for tpl in templates}
        assert scenario.template_id in ids, (
            f"{scenario.template_id!r} not found in templates. Got: {sorted(ids - {None})}"
        )

        # 2. Create request
        create_resp = await rest_client.post(
            "/api/v1/machines/request",
            json={"template_id": scenario.template_id, "count": scenario.capacity},
        )
        assert create_resp.status_code == 202
        request_id = (
            create_resp.json().get("requestId") or create_resp.json().get("request_id")
        )
        assert request_id
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

        # 3. Check status
        status_resp = await rest_client.get(f"/api/v1/requests/{request_id}/status")
        assert status_resp.status_code == 200
        status_body = status_resp.json()
        requests_list = status_body.get("requests", [])
        if requests_list:
            status = requests_list[0].get("status", "unknown")
        else:
            status = status_body.get("status", "unknown")
        assert status in {"running", "complete", "complete_with_error", "pending", "unknown"}

        # 4. Verify request appears in list
        list_resp = await rest_client.get("/api/v1/requests/")
        assert list_resp.status_code == 200
        list_body = list_resp.json()
        if isinstance(list_body, list):
            all_requests = list_body
        else:
            all_requests = list_body.get("requests", [])
        found_ids = [
            (r.get("requestId") or r.get("request_id"))
            for r in all_requests
            if isinstance(r, dict)
        ]
        assert request_id in found_ids, (
            f"Request {request_id!r} not in list. Got: {found_ids}"
        )

        # 5. Return machines if any were provisioned
        machine_ids: list[str] = []
        if requests_list:
            for m in requests_list[0].get("machines", []):
                mid = m.get("machineId") or m.get("machine_id")
                if mid:
                    machine_ids.append(mid)

        if machine_ids:
            return_resp = await rest_client.post(
                "/api/v1/machines/return",
                json={"machine_ids": machine_ids},
            )
            assert return_resp.status_code in {200, 202}, (
                f"Return failed: {return_resp.status_code} — {return_resp.text}"
            )
            assert "message" in return_resp.json()

            # Poll for return completion
            import time

            return_body = return_resp.json()
            return_request_id = return_body.get("request_id") or return_body.get("requestId")
            if return_request_id:
                deadline = time.time() + 10
                while time.time() < deadline:
                    list_resp = await rest_client.get("/api/v1/requests/return")
                    if list_resp.status_code == 200:
                        body = list_resp.json()
                        requests = body if isinstance(body, list) else body.get("requests", [])
                        done = any(
                            (r.get("request_id") or r.get("requestId")) == return_request_id
                            and r.get("status") == "complete"
                            for r in requests
                            if isinstance(r, dict)
                        )
                        if done:
                            break
                    import asyncio
                    await asyncio.sleep(0.5)
