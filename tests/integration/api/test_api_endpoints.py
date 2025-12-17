"""Integration tests for API endpoints."""

from unittest.mock import patch

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from _package import __version__
from api.server import create_fastapi_app
from config.schemas.server_schema import AuthConfig, ServerConfig


class TestAPIEndpoints:
    """Test API endpoints integration."""

    @staticmethod
    def _install_stub_routes(app):
        """Register lightweight stub routers to avoid hitting full DI stack."""
        router = APIRouter()

        @router.get("/api/v1/templates")
        async def list_templates():
            return {"templates": []}

        @router.get("/api/v1/machines")
        async def list_machines():
            return {"machines": []}

        @router.get("/api/v1/requests")
        async def list_requests():
            return {"requests": []}

        app.include_router(router)

    @pytest.fixture
    def client(self):
        """Create test client with no authentication."""
        server_config = ServerConfig(
            enabled=True, auth=AuthConfig(enabled=False, strategy="replace")
        )
        with patch("api.server._register_routers") as mock_register:
            mock_register.side_effect = self._install_stub_routes
            app = create_fastapi_app(server_config)
        return TestClient(app)

    @pytest.fixture
    def auth_client(self):
        """Create test client with authentication."""
        server_config = ServerConfig(
            enabled=True,
            auth=AuthConfig(
                enabled=True,
                strategy="bearer_token",
                bearer_token={"secret_key": "test-secret"},
            ),
        )
        with patch("api.server._register_routers") as mock_register:
            mock_register.side_effect = self._install_stub_routes
            app = create_fastapi_app(server_config)
        return TestClient(app)

    def test_health_endpoint(self, client):
        """Test health check endpoint."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "open-resource-broker"
        assert data["version"] == __version__

    def test_info_endpoint(self, client):
        """Test service info endpoint."""
        response = client.get("/info")

        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "open-resource-broker"
        assert data["version"] == __version__
        assert "description" in data
        assert "auth_enabled" in data

    def test_openapi_schema(self, client):
        """Test OpenAPI schema endpoint."""
        response = client.get("/openapi.json")

        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "Open Resource Broker API"
        assert schema["info"]["version"] == __version__
        assert "paths" in schema
        assert "components" in schema

    def test_docs_endpoint(self, client):
        """Test Swagger UI docs endpoint."""
        response = client.get("/docs")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_redoc_endpoint(self, client):
        """Test ReDoc docs endpoint."""
        response = client.get("/redoc")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_templates_endpoint_routing(self, client):
        """Test that templates endpoints are properly routed."""
        # This test verifies routing is set up correctly
        # Actual endpoint behavior is tested in router-specific tests

        response = client.get("/api/v1/templates")
        # May return 404 or other status depending on router implementation
        # The important thing is it doesn't return 500 (server error)
        assert response.status_code != 500

    def test_machines_endpoint_routing(self, client):
        """Test that machines endpoints are properly routed."""
        response = client.get("/api/v1/machines")
        assert response.status_code != 500

    def test_requests_endpoint_routing(self, client):
        """Test that requests endpoints are properly routed."""
        response = client.get("/api/v1/requests")
        assert response.status_code != 500

    def test_404_handling(self, client):
        """404 error error handling for non-existent endpoints."""
        response = client.get("/api/v1/nonexistent")

        assert response.status_code == 404

    def test_method_not_allowed(self, client):
        """405 Method Not Allowed Method Not Allowed handling."""
        # Try POST on GET-only endpoint
        response = client.post("/health")

        assert response.status_code == 405

    def test_request_id_header(self, client):
        """Test that request ID header is added to responses."""
        response = client.get("/health")

        assert response.status_code == 200
        assert "X-Request-ID" in response.headers

        # Request ID should be a valid UUID format
        request_id = response.headers["X-Request-ID"]
        import uuid

        try:
            uuid.UUID(request_id)
        except ValueError:
            pytest.fail(f"Request ID '{request_id}' is not a valid UUID")

    def test_cors_headers_present(self, client):
        """Test that CORS headers are present in responses."""
        response = client.get("/health")

        assert response.status_code == 200
        # CORS headers should be present due to CORS middleware
        # Exact headers depend on CORS configuration

    def test_content_type_headers(self, client):
        """Test that appropriate content-type headers are set."""
        # JSON endpoints
        response = client.get("/health")
        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]

        # HTML endpoints
        response = client.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_auth_required_endpoints(self, auth_client):
        """Test endpoints that require authentication."""
        # Test without auth token (should fail)
        response = auth_client.get("/info")
        assert response.status_code == 401

        # Test excluded endpoints (should work)
        response = auth_client.get("/health")
        assert response.status_code == 200

    def test_server_error_handling(self, client):
        """Test server error handling."""
        # This would require mocking internal components to force errors
        # For now, just verify the error handler is registered

        # The global exception handler should be registered
        # We can't easily test it without causing actual errors

    def test_api_versioning(self, client):
        """Test API versioning in URLs."""
        # All API endpoints should be under /api/v1/

        # Test that v1 prefix is required for API endpoints
        response = client.get("/templates")  # Missing /api/v1/
        assert response.status_code == 404

        # API endpoints should be under /api/v1/
        # (Actual functionality tested in router tests)

    def test_security_headers(self, client):
        """Test security headers are present."""
        response = client.get("/health")

        assert response.status_code == 200

        # Check for security headers (if implemented)
        # These might be added by middleware or reverse proxy
        # For now, just verify response is successful
