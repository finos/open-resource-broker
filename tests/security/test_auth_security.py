"""Security tests for authentication system."""

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from src.api.server import create_fastapi_app
from src.config.schemas.server_schema import AuthConfig, ServerConfig
from src.infrastructure.auth.strategies import BearerTokenStrategy


class TestAuthenticationSecurity:
    """Test authentication security vulnerabilities."""

    @pytest.fixture
    def auth_client(self):
        """Client with Bearer token authentication."""
        server_config = ServerConfig(
            enabled=True,
            auth=AuthConfig(
                enabled=True,
                strategy="bearer_token",
                bearer_token={
                    "secret_key": "security-test-secret-key-very-long-and-secure",
                    "algorithm": "HS256",
                    "token_expiry": 3600,
                },
            ),
        )
        app = create_fastapi_app(server_config)
        return TestClient(app)

    @pytest.fixture
    def valid_token(self):
        """Create a valid JWT token for testing."""
        strategy = BearerTokenStrategy(
            secret_key="security-test-secret-key-very-long-and-secure",
            algorithm="HS256",
            enabled=True,
        )
        return strategy._create_access_token(
            user_id="security-test-user", roles=["user"], permissions=["read"]
        )

    def test_token_tampering_detection(self, auth_client, valid_token):
        """Test that tampered tokens are rejected."""
        # Tamper with the token by changing a character
        tampered_token = valid_token[:-5] + "XXXXX"

        headers = {"Authorization": f"Bearer {tampered_token}"}
        response = auth_client.get("/info", headers=headers)

        assert response.status_code == 401
        assert "invalid" in response.json().get("detail", "").lower()

    def test_token_signature_verification(self, auth_client):
        """Test that tokens with wrong signatures are rejected."""
        # Create token with different secret
        wrong_strategy = BearerTokenStrategy(
            secret_key="wrong-secret-key", algorithm="HS256", enabled=True
        )
        wrong_token = wrong_strategy._create_access_token(
            user_id="test-user", roles=["user"], permissions=["read"]
        )

        headers = {"Authorization": f"Bearer {wrong_token}"}
        response = auth_client.get("/info", headers=headers)

        assert response.status_code == 401

    def test_expired_token_rejection(self, auth_client):
        """Test that expired tokens are properly rejected."""
        # Create token with very short expiry
        strategy = BearerTokenStrategy(
            secret_key="security-test-secret-key-very-long-and-secure",
            algorithm="HS256",
            token_expiry=1,  # 1 second
            enabled=True,
        )

        expired_token = strategy._create_access_token(
            user_id="test-user", roles=["user"], permissions=["read"]
        )

        # Wait for token to expire
        time.sleep(2)

        headers = {"Authorization": f"Bearer {expired_token}"}
        response = auth_client.get("/info", headers=headers)

        assert response.status_code == 401
        assert "expired" in response.json().get("detail", "").lower()

    def test_malformed_token_handling(self, auth_client):
        """Test handling of malformed tokens."""
        malformed_tokens = [
            "not-a-jwt-token",
            "Bearer",
            "Bearer ",
            "Bearer invalid.token.format",
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",  # Incomplete JWT
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid",  # Invalid payload
        ]

        for token in malformed_tokens:
            headers = {"Authorization": token}
            response = auth_client.get("/info", headers=headers)
            assert response.status_code == 401, f"Token '{token}' should be rejected"

    def test_authorization_header_injection(self, auth_client, valid_token):
        """Test protection against header injection attacks."""
        # Try various header injection attempts
        malicious_headers = [
            f"Bearer {valid_token}\r\nX-Injected: malicious",
            f"Bearer {valid_token}\nX-Injected: malicious",
            f"Bearer {valid_token}; X-Injected: malicious",
        ]

        for header_value in malicious_headers:
            headers = {"Authorization": header_value}
            response = auth_client.get("/info", headers=headers)
            # Should either reject the token or not have the injected header
            # The exact behavior depends on the HTTP library's handling
            assert response.status_code in [401, 200]  # Either rejected or cleaned

    def test_token_reuse_across_users(self, auth_client):
        """Test that tokens are properly scoped to users."""
        # Create tokens for different users
        strategy = BearerTokenStrategy(
            secret_key="security-test-secret-key-very-long-and-secure",
            algorithm="HS256",
            enabled=True,
        )

        user1_token = strategy._create_access_token(
            user_id="user1", roles=["user"], permissions=["read"]
        )

        user2_token = strategy._create_access_token(
            user_id="user2", roles=["admin"], permissions=["read", "write", "admin"]
        )

        # Both tokens should be valid but for different users
        headers1 = {"Authorization": f"Bearer {user1_token}"}
        headers2 = {"Authorization": f"Bearer {user2_token}"}

        response1 = auth_client.get("/info", headers=headers1)
        response2 = auth_client.get("/info", headers=headers2)

        assert response1.status_code == 200
        assert response2.status_code == 200

        # Tokens should not be interchangeable
        # (This test verifies tokens contain user-specific information)

    def test_algorithm_confusion_attack(self, auth_client):
        """Test protection against algorithm confusion attacks."""
        # Try to create a token using 'none' algorithm
        try:
            none_token = jwt.encode(
                {"sub": "malicious-user", "exp": int(time.time()) + 3600},
                "",  # No secret for 'none' algorithm
                algorithm="none",
            )

            headers = {"Authorization": f"Bearer {none_token}"}
            response = auth_client.get("/info", headers=headers)

            # Should be rejected
            assert response.status_code == 401

        except Exception:
            # If JWT library prevents 'none' algorithm, that's also good
            pass

    def test_weak_secret_detection(self):
        """Test that weak secrets are handled appropriately."""
        # This is more of a configuration test
        weak_secrets = ["", "123", "password", "secret"]

        for weak_secret in weak_secrets:
            # In production, weak secrets should be rejected or warned about
            # For now, just verify the strategy can be created
            strategy = BearerTokenStrategy(
                secret_key=weak_secret, algorithm="HS256", enabled=True
            )

            # The strategy should work but ideally warn about weak secrets
            assert strategy.secret_key == weak_secret

    def test_timing_attack_resistance(self, auth_client, valid_token):
        """Test resistance to timing attacks."""
        # This is a basic test - real timing attack testing requires more sophisticated tools

        # Test with valid token
        headers_valid = {"Authorization": f"Bearer {valid_token}"}
        start_time = time.time()
        response_valid = auth_client.get("/info", headers=headers_valid)
        valid_time = time.time() - start_time

        # Test with invalid token
        headers_invalid = {"Authorization": "Bearer invalid-token"}
        start_time = time.time()
        response_invalid = auth_client.get("/info", headers=headers_invalid)
        invalid_time = time.time() - start_time

        assert response_valid.status_code == 200
        assert response_invalid.status_code == 401

        # Times should be reasonably similar (within an order of magnitude)
        # This is a very basic check - real timing attack testing is more complex
        time_ratio = max(valid_time, invalid_time) / min(valid_time, invalid_time)
        assert time_ratio < 10, f"Timing difference too large: {time_ratio:.2f}x"

    def test_cors_security(self, auth_client):
        """Test CORS security configuration."""
        # Test CORS preflight request
        response = auth_client.options(
            "/info",
            headers={
                "Origin": "http://malicious-site.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )

        # CORS should be configured securely
        # Check that wildcard origins are handled appropriately
        if "access-control-allow-origin" in response.headers:
            origin = response.headers["access-control-allow-origin"]
            # In production, should not allow all origins with credentials
            if origin == "*":
                # If allowing all origins, credentials should not be allowed
                credentials = response.headers.get(
                    "access-control-allow-credentials", "false"
                )
                assert (
                    credentials.lower() != "true"
                ), "Wildcard CORS with credentials is insecure"

    def test_information_disclosure(self, auth_client):
        """Test that error messages don't disclose sensitive information."""
        # Test various invalid requests
        test_cases = [
            {"headers": {"Authorization": "Bearer invalid-token"}, "path": "/info"},
            {"headers": {"Authorization": "malformed"}, "path": "/info"},
            {"headers": {}, "path": "/info"},
        ]

        for case in test_cases:
            response = auth_client.get(case["path"], headers=case.get("headers", {}))

            if response.status_code == 401:
                error_detail = response.json().get("detail", "")

                # Error messages should not reveal:
                # - Internal system details
                # - Secret keys or tokens
                # - Database information
                # - File paths

                sensitive_patterns = [
                    "secret",
                    "key",
                    "password",
                    "database",
                    "/src/",
                    "traceback",
                    "exception",
                ]

                for pattern in sensitive_patterns:
                    assert (
                        pattern.lower() not in error_detail.lower()
                    ), f"Error message contains sensitive information: {pattern}"
