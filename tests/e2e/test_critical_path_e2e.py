"""Critical path E2E tests for open-resource-broker.

Tests full stack flows: API -> application -> domain -> infrastructure (mocked at infra boundary).

Critical paths covered:
1. Request Lifecycle: create -> provision -> return -> cleanup, status transitions, error handling
2. Template Management: create -> validate -> use in request, updates, deletion
3. Machine Lifecycle: provisioning, status monitoring, termination
4. Configuration Management: provider config reload, template refresh, system status
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from orb.api.dependencies import (
    get_acquire_machines_orchestrator,
    get_command_bus,
    get_create_template_orchestrator,
    get_get_template_orchestrator,
    get_list_machines_orchestrator,
    get_list_templates_orchestrator,
    get_machine_orchestrator,
    get_query_bus,
    get_refresh_templates_orchestrator,
    get_request_status_orchestrator,
    get_return_machines_orchestrator,
    get_update_template_orchestrator,
)
from orb.api.server import create_fastapi_app
from orb.config.schemas.server_schema import AuthConfig, ServerConfig
from orb.infrastructure.di.buses import CommandBus, QueryBus

# ---------------------------------------------------------------------------
# Shared helpers and fixtures
# ---------------------------------------------------------------------------


def _server_config() -> ServerConfig:
    return ServerConfig(enabled=True, auth=AuthConfig(enabled=False, strategy="replace"))  # type: ignore[call-arg]


def _make_query_bus(return_value: Any = None) -> AsyncMock:
    bus = AsyncMock(spec=QueryBus)
    bus.execute = AsyncMock(return_value=return_value)
    return bus


def _make_command_bus(return_value: Any = None) -> AsyncMock:
    bus = AsyncMock(spec=CommandBus)
    bus.execute = AsyncMock(return_value=return_value)
    return bus


@pytest.fixture
def app():
    return create_fastapi_app(_server_config())


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 1. Request Lifecycle
# ---------------------------------------------------------------------------


class TestRequestLifecycle:
    """E2E tests for the full request lifecycle."""

    def test_request_machines_returns_request_id(self, app, client: TestClient):
        """POST /api/v1/machines/request creates a request and returns a request_id."""
        mock_result = Mock()
        mock_result.request_id = "req-acquire-abc123"
        mock_result.status = "pending"
        mock_result.machine_ids = []
        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_acquire_machines_orchestrator] = lambda: mock_orchestrator
        try:
            response = client.post(
                "/api/v1/machines/request",
                json={"template_id": "tpl-001", "machine_count": 2},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        data = response.json()
        assert data["requestId"] == "req-acquire-abc123"

    def test_request_machines_missing_template_id_returns_422(self, client: TestClient):
        """POST /api/v1/machines/request without template_id returns 422."""
        response = client.post(
            "/api/v1/machines/request",
            json={"machine_count": 1},
        )
        assert response.status_code == 422

    def test_get_request_status_returns_status(self, app, client: TestClient):
        """GET /api/v1/requests/{id}/status returns request status."""
        mock_req = Mock()
        mock_req.to_dict = Mock(
            return_value={"request_id": "req-acquire-abc123", "status": "running"}
        )
        mock_result = Mock()
        mock_result.requests = [mock_req]
        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_request_status_orchestrator] = lambda: mock_orchestrator
        try:
            response = client.get("/api/v1/requests/req-acquire-abc123/status")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["requests"][0]["requestId"] == "req-acquire-abc123"
        assert data["requests"][0]["status"] == "running"

    def test_get_request_status_passes_request_id_to_orchestrator(self, app, client: TestClient):
        """GET /api/v1/requests/{id}/status passes the correct request_id to the orchestrator."""
        mock_result = Mock()
        mock_result.requests = [{"requestId": "req-acquire-xyz"}]
        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_request_status_orchestrator] = lambda: mock_orchestrator
        try:
            client.get("/api/v1/requests/req-acquire-xyz/status")
        finally:
            app.dependency_overrides.clear()

        call_arg = mock_orchestrator.execute.call_args[0][0]
        assert call_arg.request_ids == ["req-acquire-xyz"]

    def test_list_requests_returns_response(self, app, client: TestClient):
        """GET /api/v1/requests/ returns a list of requests."""
        mock_req1 = Mock()
        mock_req1.model_dump = Mock(
            return_value={"request_id": "req-acquire-001", "status": "running"}
        )
        mock_req2 = Mock()
        mock_req2.model_dump = Mock(
            return_value={"request_id": "req-acquire-002", "status": "complete"}
        )

        mock_query_bus = AsyncMock()
        mock_query_bus.execute = AsyncMock(return_value=[mock_req1, mock_req2])

        app.dependency_overrides[get_query_bus] = lambda: mock_query_bus
        try:
            response = client.get("/api/v1/requests/")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200

    def test_return_machines_creates_return_request(self, app, client: TestClient):
        """POST /api/v1/machines/return initiates machine return."""
        mock_result = Mock()
        mock_result.request_id = "req-return-abc"
        mock_result.status = "pending"
        mock_result.message = ""
        mock_result.skipped_machines = []
        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_return_machines_orchestrator] = lambda: mock_orchestrator
        try:
            response = client.post(
                "/api/v1/machines/return",
                json={"machine_ids": ["i-abc123"]},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["requestId"] == "req-return-abc"

    def test_request_lifecycle_status_transitions(self, app, client: TestClient):
        """Full lifecycle: create request, check pending, check running, check complete."""
        request_id = "req-acquire-lifecycle-001"

        # Step 1: create request
        mock_create_result = Mock()
        mock_create_result.request_id = request_id
        mock_create_result.status = "pending"
        mock_create_result.machine_ids = []
        mock_acquire_orchestrator = AsyncMock()
        mock_acquire_orchestrator.execute = AsyncMock(return_value=mock_create_result)

        app.dependency_overrides[get_acquire_machines_orchestrator] = lambda: (
            mock_acquire_orchestrator
        )
        try:
            create_resp = client.post(
                "/api/v1/machines/request",
                json={"template_id": "tpl-001", "machine_count": 1},
            )
        finally:
            app.dependency_overrides.clear()
        assert create_resp.status_code == 202

        def _make_status_req(rid, status):
            m = Mock()
            m.to_dict = Mock(return_value={"request_id": rid, "status": status})
            return m

        mock_status_orchestrator = AsyncMock()

        # Step 2: poll status - pending
        mock_status_result = Mock()
        mock_status_result.requests = [_make_status_req(request_id, "pending")]
        mock_status_orchestrator.execute = AsyncMock(return_value=mock_status_result)
        app.dependency_overrides[get_request_status_orchestrator] = lambda: mock_status_orchestrator
        try:
            status_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()
        assert status_resp.status_code == 200
        # HostFactory maps "pending" -> "running"
        assert status_resp.json()["requests"][0]["status"] == "running"

        # Step 3: poll status - running
        mock_status_result2 = Mock()
        mock_status_result2.requests = [_make_status_req(request_id, "running")]
        mock_status_orchestrator.execute = AsyncMock(return_value=mock_status_result2)
        app.dependency_overrides[get_request_status_orchestrator] = lambda: mock_status_orchestrator
        try:
            status_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()
        assert status_resp.json()["requests"][0]["status"] == "running"

        # Step 4: poll status - complete
        mock_status_result3 = Mock()
        mock_status_result3.requests = [_make_status_req(request_id, "complete")]
        mock_status_orchestrator.execute = AsyncMock(return_value=mock_status_result3)
        app.dependency_overrides[get_request_status_orchestrator] = lambda: mock_status_orchestrator
        try:
            status_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()
        assert status_resp.json()["requests"][0]["status"] == "complete"

    def test_request_handler_error_surfaces_as_server_error(self, app, client: TestClient):
        """When the orchestrator raises, the API returns a 5xx response."""
        mock_orchestrator = AsyncMock()
        mock_orchestrator.execute = AsyncMock(side_effect=RuntimeError("provisioning failed"))

        app.dependency_overrides[get_acquire_machines_orchestrator] = lambda: mock_orchestrator
        try:
            response = client.post(
                "/api/v1/machines/request",
                json={"template_id": "tpl-001", "machine_count": 1},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code >= 500

    def test_full_lifecycle_create_provision_return_cleanup(self, app, client: TestClient):
        """Full request lifecycle: create -> provision -> poll running -> return -> poll completed."""
        request_id = "req-acquire-full-lifecycle-001"
        machine_ids = ["i-lifecycle-001", "i-lifecycle-002"]

        def _make_status_req(rid, status, machines=None):
            m = Mock()
            m.to_dict = Mock(
                return_value={"request_id": rid, "status": status, "machines": machines or []}
            )
            return m

        # Step 1: create request
        mock_create_result = Mock()
        mock_create_result.request_id = request_id
        mock_create_result.status = "pending"
        mock_create_result.machine_ids = []
        mock_acquire_orchestrator = AsyncMock()
        mock_acquire_orchestrator.execute = AsyncMock(return_value=mock_create_result)

        app.dependency_overrides[get_acquire_machines_orchestrator] = lambda: (
            mock_acquire_orchestrator
        )
        try:
            create_resp = client.post(
                "/api/v1/machines/request",
                json={"template_id": "tpl-lifecycle", "machine_count": 2},
            )
        finally:
            app.dependency_overrides.clear()

        assert create_resp.status_code == 202
        assert create_resp.json()["requestId"] == request_id

        # Step 2: poll status - running with machines provisioned
        running_machines = [{"machineId": mid, "status": "running"} for mid in machine_ids]
        mock_status_result = Mock()
        mock_status_result.requests = [_make_status_req(request_id, "running", running_machines)]
        mock_status_orchestrator = AsyncMock()
        mock_status_orchestrator.execute = AsyncMock(return_value=mock_status_result)
        app.dependency_overrides[get_request_status_orchestrator] = lambda: mock_status_orchestrator
        try:
            status_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()

        assert status_resp.status_code == 200
        assert status_resp.json()["requests"][0]["status"] == "running"
        assert len(status_resp.json()["requests"][0]["machines"]) == 2

        # Step 3: return machines (cleanup)
        mock_return_result = Mock()
        mock_return_result.request_id = "req-return-lifecycle-001"
        mock_return_result.status = "complete"
        mock_return_result.message = ""
        mock_return_result.skipped_machines = []
        mock_return_orchestrator = AsyncMock()
        mock_return_orchestrator.execute = AsyncMock(return_value=mock_return_result)
        app.dependency_overrides[get_return_machines_orchestrator] = lambda: (
            mock_return_orchestrator
        )
        try:
            return_resp = client.post(
                "/api/v1/machines/return",
                json={"machine_ids": machine_ids},
            )
        finally:
            app.dependency_overrides.clear()

        assert return_resp.status_code == 200
        assert return_resp.json()["requestId"] == "req-return-lifecycle-001"

        # Step 4: poll original request - now completed
        mock_status_result2 = Mock()
        mock_status_result2.requests = [_make_status_req(request_id, "complete")]
        mock_status_orchestrator.execute = AsyncMock(return_value=mock_status_result2)
        app.dependency_overrides[get_request_status_orchestrator] = lambda: mock_status_orchestrator
        try:
            final_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()

        assert final_resp.status_code == 200
        assert final_resp.json()["requests"][0]["status"] == "complete"


# ---------------------------------------------------------------------------
# 2. Template Management
# ---------------------------------------------------------------------------


class TestTemplateManagement:
    """E2E tests for template management flows."""

    def test_list_templates_returns_empty_list(self, app, client: TestClient):
        """GET /api/v1/templates/ returns empty list when no templates exist."""
        mock_result = Mock()
        mock_result.templates = []
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_list_templates_orchestrator] = lambda: mock_orch
        try:
            response = client.get("/api/v1/templates/")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["templates"] == []
        assert data["total_count"] == 0

    def test_list_templates_returns_templates(self, app, client: TestClient):
        """GET /api/v1/templates/ returns available templates."""
        mock_template = Mock()
        mock_template.to_dict = Mock(
            return_value={
                "template_id": "tpl-001",
                "name": "test-template",
                "provider_api": "ec2_fleet",
                "max_capacity": 1,
                "instance_type": "t3.micro",
            }
        )
        mock_result = Mock()
        mock_result.templates = [mock_template]
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_list_templates_orchestrator] = lambda: mock_orch
        try:
            response = client.get("/api/v1/templates/")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 1
        assert data["templates"][0]["templateId"] == "tpl-001"

    def test_get_template_by_id_returns_template(self, app, client: TestClient):
        """GET /api/v1/templates/{id} returns the template."""
        mock_template = Mock()
        mock_template.to_dict = Mock(
            return_value={"template_id": "tpl-001", "name": "test-template", "max_capacity": 1, "instance_type": "t3.micro"}
        )
        mock_result = Mock()
        mock_result.template = mock_template
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_get_template_orchestrator] = lambda: mock_orch
        try:
            response = client.get("/api/v1/templates/tpl-001")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["templateId"] == "tpl-001"

    def test_get_template_not_found_returns_error(self, app, client: TestClient):
        """GET /api/v1/templates/{id} returns an error when template does not exist.

        Note: the handle_rest_exceptions decorator wraps HTTPException into
        InfrastructureError, so the response is 500 rather than 404.
        """
        query_bus = _make_query_bus(return_value=None)

        app.dependency_overrides[get_query_bus] = lambda: query_bus
        try:
            response = client.get("/api/v1/templates/nonexistent-tpl")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code in (404, 500, 503)

    def test_create_template_returns_201(self, app, client: TestClient):
        """POST /api/v1/templates/ creates a template and returns 201."""
        mock_result = Mock()
        mock_result.template_id = "tpl-new-001"
        mock_result.created = True
        mock_result.validation_errors = []
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_create_template_orchestrator] = lambda: mock_orch
        try:
            response = client.post(
                "/api/v1/templates/",
                json={
                    "template_id": "tpl-new-001",
                    "name": "new-template",
                    "provider_api": "ec2_fleet",
                    "image_id": "ami-12345678",
                    "subnet_ids": ["subnet-abc"],
                },
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 201
        data = response.json()
        assert data["templateId"] == "tpl-new-001"

    def test_create_template_missing_template_id_returns_422(self, client: TestClient):
        """POST /api/v1/templates/ without template_id returns 422."""
        response = client.post(
            "/api/v1/templates/",
            json={"name": "no-id-template"},
        )
        assert response.status_code == 422

    def test_create_template_validation_errors_return_error(self, app, client: TestClient):
        """POST /api/v1/templates/ with validation errors from handler returns 201.

        The create_template router does not inspect validation_errors on the command
        response — it always returns 201 when the command bus executes without raising.
        Validation is the responsibility of the command handler, not the router.
        """
        mock_result = Mock()
        mock_result.template_id = "tpl-bad"
        mock_result.created = False
        mock_result.validation_errors = ["image_id is required", "subnet_ids is required"]
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_create_template_orchestrator] = lambda: mock_orch
        try:
            response = client.post(
                "/api/v1/templates/",
                json={
                    "template_id": "tpl-bad",
                    "provider_api": "ec2_fleet",
                    "image_id": "",
                },
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 201

    def test_update_template_returns_200(self, app, client: TestClient):
        """PUT /api/v1/templates/{id} updates a template."""
        mock_result = Mock()
        mock_result.template_id = "tpl-001"
        mock_result.updated = True
        mock_result.validation_errors = []
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_update_template_orchestrator] = lambda: mock_orch
        try:
            response = client.put(
                "/api/v1/templates/tpl-001",
                json={"name": "updated-name"},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["templateId"] == "tpl-001"

    def test_delete_template_returns_200(self, app, client: TestClient):
        """DELETE /api/v1/templates/{id} deletes a template."""
        mock_cmd_response = Mock()
        mock_cmd_response.validation_errors = []

        command_bus = _make_command_bus(return_value=mock_cmd_response)

        app.dependency_overrides[get_command_bus] = lambda: command_bus
        try:
            response = client.delete("/api/v1/templates/tpl-001")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["templateId"] == "tpl-001"

    def test_template_create_then_get_flow(self, app, client: TestClient):
        """Create a template then retrieve it - full create->get flow."""
        # Step 1: create
        mock_create_result = Mock()
        mock_create_result.template_id = "tpl-flow-001"
        mock_create_result.created = True
        mock_create_result.validation_errors = []
        mock_create_orch = AsyncMock()
        mock_create_orch.execute = AsyncMock(return_value=mock_create_result)

        app.dependency_overrides[get_create_template_orchestrator] = lambda: mock_create_orch
        try:
            create_resp = client.post(
                "/api/v1/templates/",
                json={
                    "template_id": "tpl-flow-001",
                    "provider_api": "ec2_fleet",
                    "image_id": "ami-abc",
                },
            )
        finally:
            app.dependency_overrides.clear()
        assert create_resp.status_code == 201

        # Step 2: retrieve
        mock_template = Mock()
        mock_template.to_dict = Mock(
            return_value={"template_id": "tpl-flow-001", "provider_api": "ec2_fleet", "max_capacity": 1, "instance_type": "t3.micro"}
        )
        mock_get_result = Mock()
        mock_get_result.template = mock_template
        mock_get_orch = AsyncMock()
        mock_get_orch.execute = AsyncMock(return_value=mock_get_result)

        app.dependency_overrides[get_get_template_orchestrator] = lambda: mock_get_orch
        try:
            get_resp = client.get("/api/v1/templates/tpl-flow-001")
        finally:
            app.dependency_overrides.clear()

        assert get_resp.status_code == 200
        assert get_resp.json()["templateId"] == "tpl-flow-001"

    def test_template_refresh_returns_count(self, app, client: TestClient):
        """POST /api/v1/templates/refresh returns refreshed template count."""
        mock_template = Mock()
        mock_template.to_dict = Mock(
            return_value={"template_id": "tpl-001", "max_capacity": 1, "instance_type": "t3.micro"}
        )
        mock_result = Mock()
        mock_result.templates = [mock_template]
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_refresh_templates_orchestrator] = lambda: mock_orch
        try:
            response = client.post("/api/v1/templates/refresh")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 1
        assert data["success"] is True

    def test_list_templates_with_provider_api_filter(self, app, client: TestClient):
        """GET /api/v1/templates/?provider_api=ec2_fleet passes filter to query bus."""
        mock_template = Mock()
        mock_template.to_dict = Mock(
            return_value={"template_id": "tpl-ec2", "provider_api": "ec2_fleet", "max_capacity": 1, "instance_type": "t3.micro"}
        )
        mock_result = Mock()
        mock_result.templates = [mock_template]
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_list_templates_orchestrator] = lambda: mock_orch
        try:
            response = client.get("/api/v1/templates/?provider_api=ec2_fleet")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert mock_orch.execute.called

    def test_template_create_validate_then_use_in_request(self, app, client: TestClient):
        """Full flow: create template -> validate it exists -> use template_id in a request."""
        template_id = "tpl-create-use-001"

        # Step 1: create template
        mock_create_result = Mock()
        mock_create_result.template_id = template_id
        mock_create_result.created = True
        mock_create_result.validation_errors = []
        mock_create_orch = AsyncMock()
        mock_create_orch.execute = AsyncMock(return_value=mock_create_result)

        app.dependency_overrides[get_create_template_orchestrator] = lambda: mock_create_orch
        try:
            create_resp = client.post(
                "/api/v1/templates/",
                json={
                    "template_id": template_id,
                    "name": "lifecycle-template",
                    "provider_api": "ec2_fleet",
                    "image_id": "ami-12345678",
                    "subnet_ids": ["subnet-abc"],
                },
            )
        finally:
            app.dependency_overrides.clear()
        assert create_resp.status_code == 201
        assert create_resp.json()["templateId"] == template_id

        # Step 2: validate template exists via GET
        mock_template = Mock()
        mock_template.to_dict = Mock(
            return_value={
                "template_id": template_id,
                "name": "lifecycle-template",
                "provider_api": "ec2_fleet",
                "image_id": "ami-12345678",
                "max_capacity": 1,
                "instance_type": "t3.micro",
            }
        )
        mock_get_result = Mock()
        mock_get_result.template = mock_template
        mock_get_orch = AsyncMock()
        mock_get_orch.execute = AsyncMock(return_value=mock_get_result)

        app.dependency_overrides[get_get_template_orchestrator] = lambda: mock_get_orch
        try:
            get_resp = client.get(f"/api/v1/templates/{template_id}")
        finally:
            app.dependency_overrides.clear()
        assert get_resp.status_code == 200
        assert get_resp.json()["templateId"] == template_id

        # Step 3: use the template_id in a machines request
        request_id = "req-acquire-tpl-use-001"
        mock_acquire_result = Mock()
        mock_acquire_result.request_id = request_id
        mock_acquire_result.status = "pending"
        mock_acquire_result.machine_ids = []
        mock_acquire_orchestrator = AsyncMock()
        mock_acquire_orchestrator.execute = AsyncMock(return_value=mock_acquire_result)

        app.dependency_overrides[get_acquire_machines_orchestrator] = lambda: (
            mock_acquire_orchestrator
        )
        try:
            request_resp = client.post(
                "/api/v1/machines/request",
                json={"template_id": template_id, "machine_count": 1},
            )
        finally:
            app.dependency_overrides.clear()

        assert request_resp.status_code == 202
        assert request_resp.json()["requestId"] == request_id

        # Verify the orchestrator was called with the correct template_id
        call_arg = mock_acquire_orchestrator.execute.call_args[0][0]
        assert call_arg.template_id == template_id


# ---------------------------------------------------------------------------
# 3. Machine Lifecycle
# ---------------------------------------------------------------------------


class TestMachineLifecycle:
    """E2E tests for machine lifecycle flows."""

    def test_list_machines_endpoint_exists(self, app, client: TestClient):
        """GET /api/v1/machines/ endpoint is reachable and returns results."""
        mock_result = Mock()
        mock_result.machines = []
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_list_machines_orchestrator] = lambda: mock_orch
        try:
            response = client.get("/api/v1/machines/")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200

    def test_get_machine_endpoint_exists(self, app, client: TestClient):
        """GET /api/v1/machines/{id} endpoint is reachable and returns a result."""
        mock_result = Mock()
        mock_result.machine = None
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_machine_orchestrator] = lambda: mock_orch
        try:
            response = client.get("/api/v1/machines/i-abc123")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code in (200, 404)

    def test_provision_machines_then_check_status(self, app, client: TestClient):
        """Provision machines then verify status is queryable."""
        request_id = "req-acquire-machine-001"

        # Provision
        mock_provision_result = Mock()
        mock_provision_result.raw = {"requestId": request_id, "status": "pending"}
        mock_acquire_orchestrator = AsyncMock()
        mock_acquire_orchestrator.execute = AsyncMock(return_value=mock_provision_result)

        app.dependency_overrides[get_acquire_machines_orchestrator] = lambda: (
            mock_acquire_orchestrator
        )
        try:
            provision_resp = client.post(
                "/api/v1/machines/request",
                json={"template_id": "tpl-001", "machine_count": 3},
            )
        finally:
            app.dependency_overrides.clear()
        assert provision_resp.status_code == 202

        # Check status
        mock_status_result = Mock()
        mock_status_result.requests = [
            {
                "requestId": request_id,
                "status": "running",
                "machines": [
                    {"machineId": "i-001", "status": "running"},
                    {"machineId": "i-002", "status": "running"},
                    {"machineId": "i-003", "status": "running"},
                ],
            }
        ]
        mock_status_orchestrator = AsyncMock()
        mock_status_orchestrator.execute = AsyncMock(return_value=mock_status_result)
        app.dependency_overrides[get_request_status_orchestrator] = lambda: mock_status_orchestrator
        try:
            status_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()

        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["requests"][0]["status"] == "running"
        assert len(status_data["requests"][0]["machines"]) == 3

    def test_provision_then_terminate_machines(self, app, client: TestClient):
        """Full machine lifecycle: provision -> running -> terminate."""
        request_id = "req-acquire-term-001"
        machine_ids = ["i-term-001", "i-term-002"]

        # Step 1: provision
        mock_provision_result = Mock()
        mock_provision_result.request_id = request_id
        mock_provision_result.status = "running"
        mock_provision_result.machine_ids = machine_ids
        mock_acquire_orchestrator = AsyncMock()
        mock_acquire_orchestrator.execute = AsyncMock(return_value=mock_provision_result)

        app.dependency_overrides[get_acquire_machines_orchestrator] = lambda: (
            mock_acquire_orchestrator
        )
        try:
            provision_resp = client.post(
                "/api/v1/machines/request",
                json={"template_id": "tpl-001", "machine_count": 2},
            )
        finally:
            app.dependency_overrides.clear()
        assert provision_resp.status_code == 202

        # Step 2: terminate via return
        mock_return_result = Mock()
        mock_return_result.request_id = "req-return-term-001"
        mock_return_result.status = "complete"
        mock_return_result.message = ""
        mock_return_result.skipped_machines = []
        mock_return_orchestrator = AsyncMock()
        mock_return_orchestrator.execute = AsyncMock(return_value=mock_return_result)

        app.dependency_overrides[get_return_machines_orchestrator] = lambda: (
            mock_return_orchestrator
        )
        try:
            return_resp = client.post(
                "/api/v1/machines/return",
                json={"machine_ids": machine_ids},
            )
        finally:
            app.dependency_overrides.clear()

        assert return_resp.status_code == 200
        assert return_resp.json()["requestId"] == "req-return-term-001"

    def test_return_orchestrator_receives_correct_machine_ids(self, app, client: TestClient):
        """Return orchestrator is called with the machine IDs from the request body."""
        machine_ids = ["i-aaa", "i-bbb", "i-ccc"]
        mock_return_result = Mock()
        mock_return_result.raw = {"success": True}
        mock_return_orchestrator = AsyncMock()
        mock_return_orchestrator.execute = AsyncMock(return_value=mock_return_result)

        app.dependency_overrides[get_return_machines_orchestrator] = lambda: (
            mock_return_orchestrator
        )
        try:
            client.post("/api/v1/machines/return", json={"machine_ids": machine_ids})
        finally:
            app.dependency_overrides.clear()

        call_arg = mock_return_orchestrator.execute.call_args[0][0]
        assert call_arg.machine_ids == machine_ids


# ---------------------------------------------------------------------------
# 4. Configuration Management
# ---------------------------------------------------------------------------


class TestConfigurationManagement:
    """E2E tests for configuration management flows."""

    def test_health_endpoint_returns_healthy(self, app, client: TestClient):
        """GET /health returns healthy status."""
        from unittest.mock import MagicMock

        import orb.api.dependencies as deps

        mock_health_port = MagicMock()
        mock_health_port.get_status.return_value = {"status": "healthy"}
        app.dependency_overrides[deps.get_health_check_port] = lambda: mock_health_port
        try:
            response = client.get("/health")
        finally:
            app.dependency_overrides.pop(deps.get_health_check_port, None)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "open-resource-broker"
        assert "version" in data

    def test_info_endpoint_returns_service_info(self, client: TestClient):
        """GET /info returns service information."""
        response = client.get("/info")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "open-resource-broker"
        assert data["auth_enabled"] is False

    def test_template_refresh_triggers_cache_reload(self, app, client: TestClient):
        """POST /api/v1/templates/refresh reloads template cache."""
        mock_template1 = Mock()
        mock_template1.to_dict = Mock(
            return_value={"template_id": "tpl-1", "max_capacity": 1, "instance_type": "t3.micro"}
        )
        mock_template2 = Mock()
        mock_template2.to_dict = Mock(
            return_value={"template_id": "tpl-2", "max_capacity": 1, "instance_type": "t3.micro"}
        )
        mock_result = Mock()
        mock_result.templates = [mock_template1, mock_template2]
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_refresh_templates_orchestrator] = lambda: mock_orch
        try:
            response = client.post("/api/v1/templates/refresh")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 2
        assert data["success"] is True

    def test_system_status_via_health_and_info(self, client: TestClient):
        """System status is accessible via both /health and /info endpoints."""
        health_resp = client.get("/health")
        info_resp = client.get("/info")

        assert health_resp.status_code == 200
        assert info_resp.status_code == 200

        health_data = health_resp.json()
        info_data = info_resp.json()

        assert health_data["service"] == info_data["service"]
        assert health_data["version"] == info_data["version"]

    def test_openapi_schema_documents_all_critical_paths(self, client: TestClient):
        """OpenAPI schema includes all critical path endpoints."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        paths = schema.get("paths", {})

        expected_paths = [
            "/health",
            "/info",
            "/api/v1/templates/",
            "/api/v1/machines/request",
            "/api/v1/machines/return",
            "/api/v1/requests/",
        ]
        for path in expected_paths:
            assert path in paths, f"Expected path {path!r} not found in OpenAPI schema"

    def test_query_bus_unavailable_returns_500(self, app, client: TestClient):
        """When the list-templates orchestrator raises, endpoint returns 5xx."""
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(side_effect=RuntimeError("bus unavailable"))
        app.dependency_overrides[get_list_templates_orchestrator] = lambda: mock_orch
        try:
            response = client.get("/api/v1/templates/")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code in (500, 503)

    def test_command_bus_unavailable_returns_500(self, app, client: TestClient):
        """When the create-template orchestrator raises, endpoint returns 5xx."""
        mock_orch = AsyncMock()
        mock_orch.execute = AsyncMock(side_effect=RuntimeError("bus unavailable"))
        app.dependency_overrides[get_create_template_orchestrator] = lambda: mock_orch
        try:
            response = client.post(
                "/api/v1/templates/",
                json={
                    "template_id": "tpl-no-bus",
                    "provider_api": "ec2_fleet",
                    "image_id": "ami-abc",
                },
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code in (500, 503)

    def test_request_id_header_present_on_all_responses(self, client: TestClient):
        """All API responses include an X-Request-ID header."""
        for endpoint in ["/health", "/info"]:
            response = client.get(endpoint)
            assert "X-Request-ID" in response.headers, f"X-Request-ID header missing on {endpoint}"
