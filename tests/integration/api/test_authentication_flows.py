"""Integration tests for authentication flows."""

import asyncio
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_fastapi_app
from src.config.schemas.server_schema import AuthConfig, ServerConfig
from src.infrastructure.auth.strategies import BearerTokenStrategy, NoAuthStrategy


class TestAuthenticationFlows:
    """Test authentication flows with FastAPI integration."""

    def test_no_auth_flow(self):
        """Test API access with no authentication."""
        # Create server config with no auth
        server_config = ServerConfig(enabled=True, auth=AuthConfig(enabled=False, strategy="none"))

        # Create FastAPI app
        app = create_fastapi_app(server_config)
        client = TestClient(app)

        # Test health endpoint (should work without auth)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

        # Test info endpoint
        response = client.get("/info")
        assert response.status_code == 200
        data = response.json()
        assert data["auth_enabled"] is False
        assert data["auth_strategy"] is None

    def test_bearer_token_auth_flow(self):
        """Test API access with Bearer token authentication."""
        # Create server config with bearer token auth
        server_config = ServerConfig(
            enabled=True,
            auth=AuthConfig(
                enabled=True,
                strategy="bearer_token",
                bearer_token={
                    "secret_key": "test-secret-key-for-integration-test",
                    "algorithm": "HS256",
                    "token_expiry": 3600,
                },
            ),
        )

        # Create FastAPI app
        app = create_fastapi_app(server_config)
        client = TestClient(app)

        # Test health endpoint (should work without auth - excluded path)
        response = client.get("/health")
        assert response.status_code == 200

        # Test protected endpoint without token (should fail)
        response = client.get("/info")
        assert response.status_code == 401

        # Create valid JWT token
        strategy = BearerTokenStrategy(
            secret_key="test-secret-key-for-integration-test",
            algorithm="HS256",
            enabled=True,
        )
        token = strategy._create_access_token(
            user_id="test-user", roles=["user"], permissions=["read"]
        )

        # Test protected endpoint with valid token (should work)
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get("/info", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["auth_enabled"] is True
        assert data["auth_strategy"] == "bearer_token"

    def test_invalid_token_handling(self):
        """Test handling of invalid tokens."""
        # Create server config with bearer token auth
        server_config = ServerConfig(
            enabled=True,
            auth=AuthConfig(
                enabled=True,
                strategy="bearer_token",
                bearer_token={"secret_key": "test-secret-key", "algorithm": "HS256"},
            ),
        )

        app = create_fastapi_app(server_config)
        client = TestClient(app)

        # Test with invalid token format
        headers = {"Authorization": "Bearer invalid-token"}
        response = client.get("/info", headers=headers)
        assert response.status_code == 401

        # Test with missing Bearer prefix
        headers = {"Authorization": "invalid-token"}
        response = client.get("/info", headers=headers)
        assert response.status_code == 401

        # Test with empty authorization header
        headers = {"Authorization": ""}
        response = client.get("/info", headers=headers)
        assert response.status_code == 401

    def test_expired_token_handling(self):
        """Test handling of expired tokens."""
        # Create strategy with very short expiry
        strategy = BearerTokenStrategy(
            secret_key="test-secret-key",
            algorithm="HS256",
            token_expiry=1,  # 1 second expiry
            enabled=True,
        )

        # Create token
        token = strategy._create_access_token(
            user_id="test-user", roles=["user"], permissions=["read"]
        )

        # Wait for token to expire
        import time

        time.sleep(2)

        # Test expired token validation
        result = asyncio.run(strategy.validate_token(token))
        assert not result.is_authenticated
        assert result.status.value == "expired"

    @pytest.mark.asyncio
    async def test_auth_context_creation(self):
        """Test authentication context creation from requests."""
        from src.api.middleware.auth_middleware import AuthMiddleware

        # Create mock request
        class MockRequest:
            def __init__(self):
                self.method = "GET"
                self.url = Mock()
                self.url.path = "/api/v1/templates"
                self.headers = {"authorization": "Bearer test-token"}
                self.query_params = {"limit": "10"}
                self.client = Mock()
                self.client.host = "127.0.0.1"

        # Create auth middleware
        auth_strategy = NoAuthStrategy(enabled=False)
        middleware = AuthMiddleware(app=Mock(), auth_port=auth_strategy, require_auth=False)

        # Test context creation
        request = MockRequest()
        context = middleware._create_auth_context(request)

        assert context.method == "GET"
        assert context.path == "/api/v1/templates"
        assert context.headers["authorization"] == "Bearer test-token"
        assert context.query_params["limit"] == "10"
        assert context.client_ip == "127.0.0.1"

    def test_excluded_paths(self):
        """Test that excluded paths bypass authentication."""
        server_config = ServerConfig(
            enabled=True,
            auth=AuthConfig(
                enabled=True,
                strategy="bearer_token",
                bearer_token={"secret_key": "test-key"},
            ),
        )

        app = create_fastapi_app(server_config)
        client = TestClient(app)

        # Test excluded paths (should work without auth)
        excluded_paths = ["/health", "/docs", "/redoc", "/openapi.json"]

        for path in excluded_paths:
            response = client.get(path)
            # Should not return 401 (may return 404 if endpoint doesn't exist)
            assert response.status_code != 401, f"Path {path} should be excluded from auth"

    def test_cors_headers(self):
        """Test CORS headers are properly set."""
        server_config = ServerConfig(enabled=True, auth=AuthConfig(enabled=False))

        app = create_fastapi_app(server_config)
        client = TestClient(app)

        # Test CORS preflight request
        response = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

        # Should have CORS headers
        assert "access-control-allow-origin" in response.headers
        assert "access-control-allow-methods" in response.headers
