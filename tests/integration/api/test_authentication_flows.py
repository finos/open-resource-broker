"""Integration tests for authentication flows."""

import asyncio
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import URL

import orb.api.dependencies as deps
from orb.api.server import create_fastapi_app
from orb.config.schemas.server_schema import AuthConfig, CORSConfig, ServerConfig
from orb.infrastructure.auth.strategy.bearer_token_strategy import BearerTokenStrategy
from orb.infrastructure.auth.strategy.no_auth_strategy import NoAuthStrategy


class TestAuthenticationFlows:
    """Test authentication flows with FastAPI integration."""

    def test_no_auth_flow(self):
        """Test API access with no authentication."""
        from unittest.mock import MagicMock

        # Create server config with no auth
        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(enabled=False, strategy="replace"),  # type: ignore[call-arg]
        )

        # Create FastAPI app
        app = create_fastapi_app(server_config)

        mock_health_port = MagicMock()
        mock_health_port.get_status.return_value = {"status": "healthy"}
        app.dependency_overrides[deps.get_health_check_port] = lambda: mock_health_port

        client = TestClient(app)

        # Test health endpoint (should work without auth)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

        # Test info endpoint
        response = client.get("/info")
        assert response.status_code == 200
        data = response.json()
        # auth_enabled and auth_strategy are no longer surfaced on /info —
        # they were dropped to avoid disclosing auth configuration to
        # unauthenticated callers.  Verify absence.
        assert "auth_enabled" not in data
        assert "auth_strategy" not in data

    def test_bearer_token_auth_flow(self):
        """Test API access with Bearer token authentication."""
        # Create server config with bearer token auth
        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(  # type: ignore[call-arg]
                enabled=True,
                strategy="bearer_token",
                bearer_token={
                    "secret_key": "test-secret-key-for-integration-test",
                    "algorithm": "HS256",
                    "token_expiry": 3600,
                },
            ),
            cors=CORSConfig(origins=["*"]),  # type: ignore[call-arg]
        )

        # Create FastAPI app
        app = create_fastapi_app(server_config)
        from unittest.mock import MagicMock

        mock_health_port = MagicMock()
        mock_health_port.get_status.return_value = {"status": "healthy"}
        app.dependency_overrides[deps.get_health_check_port] = lambda: mock_health_port
        client = TestClient(app, raise_server_exceptions=False)

        # Test health endpoint (should work without auth - excluded path)
        response = client.get("/health")
        assert response.status_code == 200

        # Test protected endpoint without token (should fail with 401)
        response = client.get("/info")
        assert response.status_code == 401

        # Verify the auth strategy itself works correctly when used directly
        strategy = BearerTokenStrategy(
            secret_key="test-secret-key-for-integration-test",
            algorithm="HS256",
            enabled=True,
        )
        token = strategy._create_access_token(
            user_id="test-user", roles=["user"], permissions=["read"]
        )
        result = asyncio.run(strategy.validate_token(token))
        assert result.is_authenticated
        assert result.user_id == "test-user"

        response = client.get("/info", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

    def test_invalid_token_handling(self):
        """Test handling of invalid tokens."""
        # Create server config with bearer token auth
        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(  # type: ignore[call-arg]
                enabled=True,
                strategy="bearer_token",
                bearer_token={
                    "secret_key": "test-secret-key-minimum-32-bytes!",
                    "algorithm": "HS256",
                },
            ),
            cors=CORSConfig(origins=["*"]),  # type: ignore[call-arg]
        )

        app = create_fastapi_app(server_config)
        client = TestClient(app, raise_server_exceptions=False)

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
            secret_key="test-secret-key-minimum-32-bytes!",
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
        from orb.api.middleware.auth_middleware import AuthMiddleware

        # Create mock request
        class MockRequest:
            def __init__(self):
                self.method = "GET"
                self.url = URL("http://testserver/api/v1/templates")
                self.base_url = URL("http://testserver/")
                self.headers = {"authorization": "Bearer test-token"}
                self.query_params = {"limit": "10"}
                self.client = Mock()
                self.client.host = "127.0.0.1"

        # Create auth middleware
        auth_strategy = NoAuthStrategy(enabled=False)
        middleware = AuthMiddleware(app=Mock(), auth_port=auth_strategy, require_auth=False)

        # Test context creation
        request = MockRequest()
        context = middleware._create_auth_context(request)  # type: ignore[arg-type]

        assert context.method == "GET"
        assert context.path == "/api/v1/templates"
        assert context.headers["authorization"] == "Bearer test-token"
        assert context.query_params["limit"] == "10"
        assert context.client_ip == "127.0.0.1"

    def test_excluded_paths(self):
        """Test that /health bypasses auth and docs endpoints are gated when auth is enabled."""

        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(  # type: ignore[call-arg]
                enabled=True,
                strategy="bearer_token",
                bearer_token={"secret_key": "test-secret-key-minimum-32-bytes!"},
            ),
            cors=CORSConfig(origins=["*"]),  # type: ignore[call-arg]
        )

        app = create_fastapi_app(server_config)
        client = TestClient(app)

        # /health is always public regardless of auth configuration.
        response = client.get("/health")
        assert response.status_code != 401, "/health should never require authentication"

        # When auth is enabled and docs.require_auth=True (default), docs endpoints
        # are protected.  They should return 401 without credentials.
        for path in ["/docs", "/redoc", "/openapi.json"]:
            response = client.get(path)
            assert response.status_code == 401, (
                f"Path {path} should require authentication when auth is enabled "
                "and docs.require_auth=True (default)"
            )

    def test_excluded_paths_docs_public_when_require_auth_false(self):
        """Docs endpoints are public when docs.require_auth=False."""
        from orb.config.schemas.server_schema import DocsConfig

        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(  # type: ignore[call-arg]
                enabled=True,
                strategy="bearer_token",
                bearer_token={"secret_key": "test-secret-key-minimum-32-bytes!"},
            ),
            cors=CORSConfig(origins=["*"]),  # type: ignore[call-arg]
            docs=DocsConfig(require_auth=False),  # type: ignore[call-arg]
        )

        app = create_fastapi_app(server_config)
        client = TestClient(app)

        for path in ["/docs", "/redoc", "/openapi.json"]:
            response = client.get(path)
            assert response.status_code != 401, (
                f"Path {path} should be public when docs.require_auth=False"
            )

    def test_cors_headers(self):
        """Test CORS headers are properly set."""
        server_config = ServerConfig(  # type: ignore[call-arg]
            enabled=True,
            auth=AuthConfig(enabled=False),  # type: ignore[call-arg]
            cors=CORSConfig(origins=["http://localhost:3000"]),  # type: ignore[call-arg]
        )

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
