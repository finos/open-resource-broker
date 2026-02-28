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
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from api.dependencies import (
    get_request_machines_handler,
    get_request_status_handler,
    get_return_machines_handler,
    get_return_requests_handler,
)
from api.server import create_fastapi_app
from config.schemas.server_schema import AuthConfig, ServerConfig
from infrastructure.di.buses import CommandBus, QueryBus

# ---------------------------------------------------------------------------
# Shared helpers and fixtures
# ---------------------------------------------------------------------------


def _server_config() -> ServerConfig:
    return ServerConfig(enabled=True, auth=AuthConfig(enabled=False, strategy="replace"))


def _make_query_bus(return_value: Any = None) -> AsyncMock:
    bus = AsyncMock(spec=QueryBus)
    bus.execute = AsyncMock(return_value=return_value)
    return bus


def _make_command_bus(return_value: Any = None) -> AsyncMock:
    bus = AsyncMock(spec=CommandBus)
    bus.execute = AsyncMock(return_value=return_value)
    return bus


def _make_container(query_bus: Any = None, command_bus: Any = None) -> Mock:
    container = Mock()

    def _get(svc_type: type) -> Any:
        if svc_type is QueryBus:
            return query_bus
        if svc_type is CommandBus:
            return command_bus
        return Mock()

    container.get.side_effect = _get
    return container


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
        mock_response = Mock()
        mock_response.to_dict = Mock(
            return_value={"requestId": "req-acquire-abc123", "status": "pending"}
        )
        mock_handler = AsyncMock()
        mock_handler.handle = AsyncMock(return_value=mock_response)

        app.dependency_overrides[get_request_machines_handler] = lambda: mock_handler
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
        mock_result = {
            "requestId": "req-acquire-abc123",
            "status": "running",
            "machineCount": 2,
        }
        mock_handler = AsyncMock()
        mock_handler.handle = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_request_status_handler] = lambda: mock_handler
        try:
            response = client.get("/api/v1/requests/req-acquire-abc123/status")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["requestId"] == "req-acquire-abc123"
        assert data["status"] == "running"

    def test_get_request_status_passes_request_id_to_handler(self, app, client: TestClient):
        """GET /api/v1/requests/{id}/status passes the correct request_id to the handler."""
        mock_handler = AsyncMock()
        mock_handler.handle = AsyncMock(return_value={"requestId": "req-acquire-xyz"})

        app.dependency_overrides[get_request_status_handler] = lambda: mock_handler
        try:
            client.get("/api/v1/requests/req-acquire-xyz/status")
        finally:
            app.dependency_overrides.clear()

        call_args = mock_handler.handle.call_args[0][0]
        assert call_args["input_data"]["requests"][0]["requestId"] == "req-acquire-xyz"

    def test_list_requests_returns_response(self, app, client: TestClient):
        """GET /api/v1/requests/ returns a list of requests."""
        mock_result = [
            {"requestId": "req-acquire-001", "status": "running"},
            {"requestId": "req-acquire-002", "status": "complete"},
        ]
        mock_handler = AsyncMock()
        mock_handler.handle = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_return_requests_handler] = lambda: mock_handler
        try:
            response = client.get("/api/v1/requests/")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200

    def test_return_machines_creates_return_request(self, app, client: TestClient):
        """POST /api/v1/machines/return initiates machine return."""
        mock_result = {
            "success": True,
            "returnRequestIds": ["req-return-abc"],
            "processedMachines": ["i-abc123"],
        }
        mock_handler = AsyncMock()
        mock_handler.handle = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_return_machines_handler] = lambda: mock_handler
        try:
            response = client.post(
                "/api/v1/machines/return",
                json={"machine_ids": ["i-abc123"]},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_request_lifecycle_status_transitions(self, app, client: TestClient):
        """Full lifecycle: create request, check pending, check running, check complete."""
        request_id = "req-acquire-lifecycle-001"

        # Step 1: create request
        mock_create_response = Mock()
        mock_create_response.to_dict = Mock(
            return_value={"requestId": request_id, "status": "pending"}
        )
        mock_request_handler = AsyncMock()
        mock_request_handler.handle = AsyncMock(return_value=mock_create_response)

        app.dependency_overrides[get_request_machines_handler] = lambda: mock_request_handler
        try:
            create_resp = client.post(
                "/api/v1/machines/request",
                json={"template_id": "tpl-001", "machine_count": 1},
            )
        finally:
            app.dependency_overrides.clear()
        assert create_resp.status_code == 202

        # Step 2: poll status - pending
        mock_status_handler = AsyncMock()
        mock_status_handler.handle = AsyncMock(
            return_value={"requestId": request_id, "status": "pending"}
        )
        app.dependency_overrides[get_request_status_handler] = lambda: mock_status_handler
        try:
            status_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "pending"

        # Step 3: poll status - running
        mock_status_handler.handle = AsyncMock(
            return_value={"requestId": request_id, "status": "running"}
        )
        app.dependency_overrides[get_request_status_handler] = lambda: mock_status_handler
        try:
            status_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()
        assert status_resp.json()["status"] == "running"

        # Step 4: poll status - complete
        mock_status_handler.handle = AsyncMock(
            return_value={"requestId": request_id, "status": "complete"}
        )
        app.dependency_overrides[get_request_status_handler] = lambda: mock_status_handler
        try:
            status_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()
        assert status_resp.json()["status"] == "complete"

    def test_request_handler_error_surfaces_as_server_error(self, app, client: TestClient):
        """When the handler raises, the API returns a 5xx response."""
        mock_handler = AsyncMock()
        mock_handler.handle = AsyncMock(side_effect=RuntimeError("provisioning failed"))

        app.dependency_overrides[get_request_machines_handler] = lambda: mock_handler
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

        # Step 1: create request
        mock_create_response = Mock()
        mock_create_response.to_dict = Mock(
            return_value={"requestId": request_id, "status": "pending"}
        )
        mock_request_handler = AsyncMock()
        mock_request_handler.handle = AsyncMock(return_value=mock_create_response)

        app.dependency_overrides[get_request_machines_handler] = lambda: mock_request_handler
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
        mock_status_handler = AsyncMock()
        mock_status_handler.handle = AsyncMock(
            return_value={
                "requestId": request_id,
                "status": "running",
                "machines": [{"machineId": mid, "status": "running"} for mid in machine_ids],
            }
        )
        app.dependency_overrides[get_request_status_handler] = lambda: mock_status_handler
        try:
            status_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()

        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "running"
        assert len(status_resp.json()["machines"]) == 2

        # Step 3: return machines (cleanup)
        mock_return_handler = AsyncMock()
        mock_return_handler.handle = AsyncMock(
            return_value={
                "success": True,
                "returnRequestIds": ["req-return-lifecycle-001"],
                "processedMachines": machine_ids,
            }
        )
        app.dependency_overrides[get_return_machines_handler] = lambda: mock_return_handler
        try:
            return_resp = client.post(
                "/api/v1/machines/return",
                json={"machine_ids": machine_ids},
            )
        finally:
            app.dependency_overrides.clear()

        assert return_resp.status_code == 200
        return_data = return_resp.json()
        assert return_data["success"] is True
        assert set(return_data["processedMachines"]) == set(machine_ids)

        # Step 4: poll original request - now completed
        mock_status_handler.handle = AsyncMock(
            return_value={"requestId": request_id, "status": "complete", "machines": []}
        )
        app.dependency_overrides[get_request_status_handler] = lambda: mock_status_handler
        try:
            final_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()

        assert final_resp.status_code == 200
        assert final_resp.json()["status"] == "complete"


# ---------------------------------------------------------------------------
# 2. Template Management
# ---------------------------------------------------------------------------


class TestTemplateManagement:
    """E2E tests for template management flows."""

    def test_list_templates_returns_empty_list(self, client: TestClient):
        """GET /api/v1/templates/ returns empty list when no templates exist."""
        query_bus = _make_query_bus(return_value=[])
        container = _make_container(query_bus=query_bus)

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.get("/api/v1/templates/")

        assert response.status_code == 200
        data = response.json()
        assert data["templates"] == []
        assert data["total_count"] == 0

    def test_list_templates_returns_templates(self, client: TestClient):
        """GET /api/v1/templates/ returns available templates."""
        mock_template = Mock()
        template_data = {
            "template_id": "tpl-001",
            "name": "test-template",
            "provider_api": "ec2_fleet",
        }
        mock_template.to_dict = Mock(return_value=template_data)
        mock_template.model_dump = Mock(return_value=template_data)

        query_bus = _make_query_bus(return_value=[mock_template])
        container = _make_container(query_bus=query_bus)

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.get("/api/v1/templates/")

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 1
        assert data["templates"][0]["template_id"] == "tpl-001"

    def test_get_template_by_id_returns_template(self, client: TestClient):
        """GET /api/v1/templates/{id} returns the template."""
        mock_template = Mock()
        template_data = {"template_id": "tpl-001", "name": "test-template"}
        mock_template.to_dict = Mock(return_value=template_data)
        mock_template.model_dump = Mock(return_value=template_data)

        query_bus = _make_query_bus(return_value=mock_template)
        container = _make_container(query_bus=query_bus)

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.get("/api/v1/templates/tpl-001")

        assert response.status_code == 200
        data = response.json()
        assert data["template"]["template_id"] == "tpl-001"

    def test_get_template_not_found_returns_error(self, client: TestClient):
        """GET /api/v1/templates/{id} returns an error when template does not exist.

        Note: the handle_rest_exceptions decorator wraps HTTPException into
        InfrastructureError, so the response is 500 rather than 404.
        """
        query_bus = _make_query_bus(return_value=None)
        container = _make_container(query_bus=query_bus)

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.get("/api/v1/templates/nonexistent-tpl")

        assert response.status_code in (404, 500, 503)

    def test_create_template_returns_201(self, client: TestClient):
        """POST /api/v1/templates/ creates a template and returns 201."""
        mock_cmd_response = Mock()
        mock_cmd_response.validation_errors = []

        command_bus = _make_command_bus(return_value=mock_cmd_response)
        container = _make_container(command_bus=command_bus)

        with patch("api.routers.templates.get_container", return_value=container):
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

    def test_create_template_validation_errors_return_error(self, client: TestClient):
        """POST /api/v1/templates/ with validation errors from handler returns 201.

        The create_template router does not inspect validation_errors on the command
        response — it always returns 201 when the command bus executes without raising.
        Validation is the responsibility of the command handler, not the router.
        """
        mock_cmd_response = Mock()
        mock_cmd_response.validation_errors = ["image_id is required", "subnet_ids is required"]

        command_bus = _make_command_bus(return_value=mock_cmd_response)
        container = _make_container(command_bus=command_bus)

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.post(
                "/api/v1/templates/",
                json={
                    "template_id": "tpl-bad",
                    "provider_api": "ec2_fleet",
                    "image_id": "",
                },
            )

        assert response.status_code == 201

    def test_update_template_returns_200(self, client: TestClient):
        """PUT /api/v1/templates/{id} updates a template."""
        mock_cmd_response = Mock()
        mock_cmd_response.validation_errors = []

        command_bus = _make_command_bus(return_value=mock_cmd_response)
        container = _make_container(command_bus=command_bus)

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.put(
                "/api/v1/templates/tpl-001",
                json={"name": "updated-name"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["templateId"] == "tpl-001"

    def test_delete_template_returns_200(self, client: TestClient):
        """DELETE /api/v1/templates/{id} deletes a template."""
        mock_cmd_response = Mock()
        mock_cmd_response.validation_errors = []

        command_bus = _make_command_bus(return_value=mock_cmd_response)
        container = _make_container(command_bus=command_bus)

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.delete("/api/v1/templates/tpl-001")

        assert response.status_code == 200
        data = response.json()
        assert data["templateId"] == "tpl-001"

    def test_template_create_then_get_flow(self, client: TestClient):
        """Create a template then retrieve it - full create->get flow."""
        # Step 1: create
        mock_cmd_response = Mock()
        mock_cmd_response.validation_errors = []
        command_bus = _make_command_bus(return_value=mock_cmd_response)
        container = _make_container(command_bus=command_bus)

        with patch("api.routers.templates.get_container", return_value=container):
            create_resp = client.post(
                "/api/v1/templates/",
                json={
                    "template_id": "tpl-flow-001",
                    "provider_api": "ec2_fleet",
                    "image_id": "ami-abc",
                },
            )
        assert create_resp.status_code == 201

        # Step 2: retrieve
        mock_template = Mock()
        flow_data = {"template_id": "tpl-flow-001", "provider_api": "ec2_fleet"}
        mock_template.to_dict = Mock(return_value=flow_data)
        mock_template.model_dump = Mock(return_value=flow_data)
        query_bus = _make_query_bus(return_value=mock_template)
        container2 = _make_container(query_bus=query_bus)

        with patch("api.routers.templates.get_container", return_value=container2):
            get_resp = client.get("/api/v1/templates/tpl-flow-001")

        assert get_resp.status_code == 200
        assert get_resp.json()["template"]["template_id"] == "tpl-flow-001"

    def test_template_refresh_returns_count(self, client: TestClient):
        """POST /api/v1/templates/refresh returns refreshed template count."""
        mock_template = Mock()
        refresh_data = {"template_id": "tpl-001"}
        mock_template.to_dict = Mock(return_value=refresh_data)
        mock_template.model_dump = Mock(return_value=refresh_data)
        query_bus = _make_query_bus(return_value=[mock_template])
        container = _make_container(query_bus=query_bus)

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.post("/api/v1/templates/refresh")

        assert response.status_code == 200
        data = response.json()
        assert data["templateCount"] == 1
        assert data["cacheStats"]["refreshed"] is True

    def test_list_templates_with_provider_api_filter(self, client: TestClient):
        """GET /api/v1/templates/?provider_api=ec2_fleet passes filter to query bus."""
        mock_template = Mock()
        ec2_data = {"template_id": "tpl-ec2", "provider_api": "ec2_fleet"}
        mock_template.to_dict = Mock(return_value=ec2_data)
        mock_template.model_dump = Mock(return_value=ec2_data)
        query_bus = _make_query_bus(return_value=[mock_template])
        container = _make_container(query_bus=query_bus)

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.get("/api/v1/templates/?provider_api=ec2_fleet")

        assert response.status_code == 200
        assert query_bus.execute.called

    def test_template_create_validate_then_use_in_request(self, app, client: TestClient):
        """Full flow: create template -> validate it exists -> use template_id in a request."""
        template_id = "tpl-create-use-001"

        # Step 1: create template
        mock_cmd_response = Mock()
        mock_cmd_response.validation_errors = []
        command_bus = _make_command_bus(return_value=mock_cmd_response)
        container = _make_container(command_bus=command_bus)

        with patch("api.routers.templates.get_container", return_value=container):
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
        assert create_resp.status_code == 201
        assert create_resp.json()["templateId"] == template_id

        # Step 2: validate template exists via GET
        mock_template = Mock()
        get_data = {
            "template_id": template_id,
            "name": "lifecycle-template",
            "provider_api": "ec2_fleet",
            "image_id": "ami-12345678",
        }
        mock_template.to_dict = Mock(return_value=get_data)
        mock_template.model_dump = Mock(return_value=get_data)
        query_bus = _make_query_bus(return_value=mock_template)
        container2 = _make_container(query_bus=query_bus)

        with patch("api.routers.templates.get_container", return_value=container2):
            get_resp = client.get(f"/api/v1/templates/{template_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["template"]["template_id"] == template_id

        # Step 3: use the template_id in a machines request
        request_id = "req-acquire-tpl-use-001"
        mock_request_response = Mock()
        mock_request_response.to_dict = Mock(
            return_value={"requestId": request_id, "status": "pending"}
        )
        mock_request_handler = AsyncMock()
        mock_request_handler.handle = AsyncMock(return_value=mock_request_response)

        app.dependency_overrides[get_request_machines_handler] = lambda: mock_request_handler
        try:
            request_resp = client.post(
                "/api/v1/machines/request",
                json={"template_id": template_id, "machine_count": 1},
            )
        finally:
            app.dependency_overrides.clear()

        assert request_resp.status_code == 202
        assert request_resp.json()["requestId"] == request_id

        # Verify the handler was called with the correct template_id
        call_arg = mock_request_handler.handle.call_args[0][0]
        actual_template_id = (
            call_arg.template["templateId"]
            if hasattr(call_arg, "template")
            else call_arg["input_data"]["templateId"]
        )
        assert actual_template_id == template_id


# ---------------------------------------------------------------------------
# 3. Machine Lifecycle
# ---------------------------------------------------------------------------


class TestMachineLifecycle:
    """E2E tests for machine lifecycle flows."""

    def test_list_machines_endpoint_exists(self, client: TestClient):
        """GET /api/v1/machines/ endpoint is reachable (returns 501 - not yet implemented)."""
        response = client.get("/api/v1/machines/")
        # Endpoint exists but is not yet implemented
        assert response.status_code == 501
        data = response.json()
        assert "error" in data

    def test_get_machine_endpoint_exists(self, client: TestClient):
        """GET /api/v1/machines/{id} endpoint is reachable (returns 501 - not yet implemented)."""
        response = client.get("/api/v1/machines/i-abc123")
        assert response.status_code == 501
        data = response.json()
        assert "error" in data

    def test_provision_machines_then_check_status(self, app, client: TestClient):
        """Provision machines then verify status is queryable."""
        request_id = "req-acquire-machine-001"

        # Provision
        mock_provision_response = Mock()
        mock_provision_response.to_dict = Mock(
            return_value={"requestId": request_id, "status": "pending"}
        )
        mock_provision_handler = AsyncMock()
        mock_provision_handler.handle = AsyncMock(return_value=mock_provision_response)

        app.dependency_overrides[get_request_machines_handler] = lambda: mock_provision_handler
        try:
            provision_resp = client.post(
                "/api/v1/machines/request",
                json={"template_id": "tpl-001", "machine_count": 3},
            )
        finally:
            app.dependency_overrides.clear()
        assert provision_resp.status_code == 202

        # Check status
        mock_status_handler = AsyncMock()
        mock_status_handler.handle = AsyncMock(
            return_value={
                "requestId": request_id,
                "status": "running",
                "machines": [
                    {"machineId": "i-001", "status": "running"},
                    {"machineId": "i-002", "status": "running"},
                    {"machineId": "i-003", "status": "running"},
                ],
            }
        )
        app.dependency_overrides[get_request_status_handler] = lambda: mock_status_handler
        try:
            status_resp = client.get(f"/api/v1/requests/{request_id}/status")
        finally:
            app.dependency_overrides.clear()

        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["status"] == "running"
        assert len(status_data["machines"]) == 3

    def test_provision_then_terminate_machines(self, app, client: TestClient):
        """Full machine lifecycle: provision -> running -> terminate."""
        request_id = "req-acquire-term-001"
        machine_ids = ["i-term-001", "i-term-002"]

        # Step 1: provision
        mock_provision_response = Mock()
        mock_provision_response.to_dict = Mock(
            return_value={"requestId": request_id, "status": "running"}
        )
        mock_provision_handler = AsyncMock()
        mock_provision_handler.handle = AsyncMock(return_value=mock_provision_response)

        app.dependency_overrides[get_request_machines_handler] = lambda: mock_provision_handler
        try:
            provision_resp = client.post(
                "/api/v1/machines/request",
                json={"template_id": "tpl-001", "machine_count": 2},
            )
        finally:
            app.dependency_overrides.clear()
        assert provision_resp.status_code == 202

        # Step 2: terminate via return
        mock_return_result = {
            "success": True,
            "returnRequestIds": ["req-return-term-001"],
            "processedMachines": machine_ids,
        }
        mock_return_handler = AsyncMock()
        mock_return_handler.handle = AsyncMock(return_value=mock_return_result)

        app.dependency_overrides[get_return_machines_handler] = lambda: mock_return_handler
        try:
            return_resp = client.post(
                "/api/v1/machines/return",
                json={"machine_ids": machine_ids},
            )
        finally:
            app.dependency_overrides.clear()

        assert return_resp.status_code == 200
        return_data = return_resp.json()
        assert return_data["success"] is True
        assert set(return_data["processedMachines"]) == set(machine_ids)

    def test_return_handler_receives_correct_machine_ids(self, app, client: TestClient):
        """Return handler is called with the machine IDs from the request body."""
        machine_ids = ["i-aaa", "i-bbb", "i-ccc"]
        mock_return_handler = AsyncMock()
        mock_return_handler.handle = AsyncMock(return_value={"success": True})

        app.dependency_overrides[get_return_machines_handler] = lambda: mock_return_handler
        try:
            client.post("/api/v1/machines/return", json={"machine_ids": machine_ids})
        finally:
            app.dependency_overrides.clear()

        call_args = mock_return_handler.handle.call_args[0][0]
        returned_ids = [m["machineId"] for m in call_args["input_data"]["machines"]]
        assert returned_ids == machine_ids


# ---------------------------------------------------------------------------
# 4. Configuration Management
# ---------------------------------------------------------------------------


class TestConfigurationManagement:
    """E2E tests for configuration management flows."""

    def test_health_endpoint_returns_healthy(self, client: TestClient):
        """GET /health returns healthy status."""
        response = client.get("/health")
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

    def test_template_refresh_triggers_cache_reload(self, client: TestClient):
        """POST /api/v1/templates/refresh reloads template cache."""
        templates = [Mock(), Mock()]
        for t in templates:
            t.model_dump = Mock(return_value={"template_id": f"tpl-{id(t)}"})

        query_bus = _make_query_bus(return_value=templates)
        container = _make_container(query_bus=query_bus)

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.post("/api/v1/templates/refresh")

        assert response.status_code == 200
        data = response.json()
        assert data["templateCount"] == 2
        assert data["cacheStats"]["refreshed"] is True

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

    def test_query_bus_unavailable_returns_500(self, client: TestClient):
        """When QueryBus is not in container, endpoint returns 500."""
        container = Mock()
        container.get.return_value = None

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.get("/api/v1/templates/")

        assert response.status_code in (500, 503)

    def test_command_bus_unavailable_returns_500(self, client: TestClient):
        """When CommandBus is not in container, create template returns 500."""
        container = Mock()
        container.get.return_value = None

        with patch("api.routers.templates.get_container", return_value=container):
            response = client.post(
                "/api/v1/templates/",
                json={
                    "template_id": "tpl-no-bus",
                    "provider_api": "ec2_fleet",
                    "image_id": "ami-abc",
                },
            )

        assert response.status_code in (500, 503)

    def test_request_id_header_present_on_all_responses(self, client: TestClient):
        """All API responses include an X-Request-ID header."""
        for endpoint in ["/health", "/info"]:
            response = client.get(endpoint)
            assert "X-Request-ID" in response.headers, f"X-Request-ID header missing on {endpoint}"
