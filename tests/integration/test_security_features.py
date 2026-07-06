"""Integration tests for security features: JWT denylist, input validation, auth middleware."""

import time
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest

from orb.infrastructure.adapters.ports.auth import AuthContext, AuthStatus
from orb.infrastructure.auth.strategy.bearer_token_strategy_enhanced import (
    EnhancedBearerTokenStrategy,
    RateLimiter,
)
from orb.infrastructure.auth.token_denylist import InMemoryTokenDenylist, RedisTokenDenylist
from orb.infrastructure.validation.input_validator import InputValidator, ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SECRET = "integration-test-secret-key-32bytes!!"  # >= 32 bytes


def _make_context(
    token: str,
    client_ip: str = "10.0.0.1",
    path: str = "/api/v1/machines",
) -> AuthContext:
    return AuthContext(
        method="GET",
        path=path,
        headers={"authorization": f"Bearer {token}"},
        query_params={},
        client_ip=client_ip,
    )


def _make_strategy(
    denylist=None,
    rate_limit_enabled: bool = False,
    token_expiry: int = 3600,
) -> EnhancedBearerTokenStrategy:
    if denylist is None:
        denylist = InMemoryTokenDenylist()
    return EnhancedBearerTokenStrategy(
        secret_key=SECRET,
        denylist=denylist,
        algorithm="HS256",
        token_expiry=token_expiry,
        enabled=True,
        rate_limit_enabled=rate_limit_enabled,
    )


# ===========================================================================
# JWT Token Denylist – InMemory
# ===========================================================================


class TestInMemoryDenylistIntegration:
    """Integration tests for InMemoryTokenDenylist."""

    @pytest.mark.asyncio
    async def test_revoke_then_reject(self):
        """Token added to denylist must be rejected by the strategy."""
        denylist = InMemoryTokenDenylist()
        strategy = _make_strategy(denylist)

        token = strategy._create_access_token("user1", ["user"], ["read"])

        # Token is valid before revocation
        result = await strategy.validate_token(token)
        assert result.is_authenticated

        # Revoke it
        revoked = await strategy.revoke_token(token)
        assert revoked is True

        # Now it must be rejected
        result = await strategy.validate_token(token)
        assert not result.is_authenticated
        assert result.status == AuthStatus.INVALID

    @pytest.mark.asyncio
    async def test_denylist_does_not_affect_other_tokens(self):
        """Revoking one token must not affect other valid tokens."""
        denylist = InMemoryTokenDenylist()
        strategy = _make_strategy(denylist)

        token_a = strategy._create_access_token("userA", ["user"], ["read"])
        token_b = strategy._create_access_token("userB", ["user"], ["read"])

        await strategy.revoke_token(token_a)

        result_a = await strategy.validate_token(token_a)
        result_b = await strategy.validate_token(token_b)

        assert not result_a.is_authenticated
        assert result_b.is_authenticated

    @pytest.mark.asyncio
    async def test_expired_denylist_entry_auto_removed(self):
        """Denylist entries with past expiry are treated as not denylisted."""
        denylist = InMemoryTokenDenylist()
        token = "some.jwt.token"
        past_expiry = int(time.time()) - 10  # already expired

        await denylist.add_token(token, expires_at=past_expiry)

        # Should NOT be denylisted because the entry itself has expired
        assert await denylist.is_denylisted(token) is False

    @pytest.mark.asyncio
    async def test_cleanup_removes_only_expired(self):
        """cleanup_expired removes expired entries and keeps valid ones."""
        denylist = InMemoryTokenDenylist()

        await denylist.add_token("expired1", int(time.time()) - 5)
        await denylist.add_token("expired2", int(time.time()) - 1)
        await denylist.add_token("valid1", int(time.time()) + 3600)

        removed = await denylist.cleanup_expired()

        assert removed == 2
        assert await denylist.is_denylisted("valid1") is True
        assert await denylist.get_denylist_size() == 1

    @pytest.mark.asyncio
    async def test_remove_token_from_denylist(self):
        """Explicitly removed token is no longer denylisted."""
        denylist = InMemoryTokenDenylist()
        token = "removable.token"

        await denylist.add_token(token)
        assert await denylist.is_denylisted(token) is True

        removed = await denylist.remove_token(token)
        assert removed is True
        assert await denylist.is_denylisted(token) is False

    @pytest.mark.asyncio
    async def test_remove_nonexistent_token_returns_false(self):
        """Removing a token that was never added returns False."""
        denylist = InMemoryTokenDenylist()
        result = await denylist.remove_token("ghost.token")
        assert result is False

    @pytest.mark.asyncio
    async def test_denylist_size_tracks_additions(self):
        """get_denylist_size reflects the number of active entries."""
        denylist = InMemoryTokenDenylist()
        assert await denylist.get_denylist_size() == 0

        await denylist.add_token("t1")
        await denylist.add_token("t2")
        await denylist.add_token("t3")

        assert await denylist.get_denylist_size() == 3


# ===========================================================================
# JWT Token Denylist – Redis (mocked)
# ===========================================================================


class TestRedisDenylistIntegration:
    """Integration tests for RedisTokenDenylist with a mocked Redis client."""

    def _make_redis_mock(self) -> MagicMock:
        redis = MagicMock()
        redis.setex = AsyncMock(return_value=True)
        redis.set = AsyncMock(return_value=True)
        redis.exists = AsyncMock(return_value=1)
        redis.delete = AsyncMock(return_value=1)
        redis.keys = AsyncMock(return_value=["token_denylist:tok1", "token_denylist:tok2"])
        return redis

    @pytest.mark.asyncio
    async def test_add_token_with_expiry_calls_setex(self):
        """add_token with expiry uses Redis SETEX."""
        redis = self._make_redis_mock()
        denylist = RedisTokenDenylist(redis_client=redis)

        future_expiry = int(time.time()) + 3600
        result = await denylist.add_token("mytoken", expires_at=future_expiry)

        assert result is True
        redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_token_without_expiry_calls_set(self):
        """add_token without expiry uses Redis SET."""
        redis = self._make_redis_mock()
        denylist = RedisTokenDenylist(redis_client=redis)

        result = await denylist.add_token("mytoken")

        assert result is True
        redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_denylisted_returns_true_when_key_exists(self):
        """is_denylisted returns True when Redis key exists."""
        redis = self._make_redis_mock()
        redis.exists = AsyncMock(return_value=1)
        denylist = RedisTokenDenylist(redis_client=redis)

        assert await denylist.is_denylisted("mytoken") is True

    @pytest.mark.asyncio
    async def test_is_denylisted_returns_false_when_key_missing(self):
        """is_denylisted returns False when Redis key does not exist."""
        redis = self._make_redis_mock()
        redis.exists = AsyncMock(return_value=0)
        denylist = RedisTokenDenylist(redis_client=redis)

        assert await denylist.is_denylisted("mytoken") is False

    @pytest.mark.asyncio
    async def test_redis_error_fails_secure(self):
        """On Redis error, is_denylisted fails secure (returns True)."""
        redis = self._make_redis_mock()
        redis.exists = AsyncMock(side_effect=ConnectionError("Redis down"))
        denylist = RedisTokenDenylist(redis_client=redis)

        # Fail-secure: assume denylisted on error
        assert await denylist.is_denylisted("anytoken") is True

    @pytest.mark.asyncio
    async def test_fallback_to_in_memory_when_no_redis(self):
        """Without a Redis client, RedisTokenDenylist falls back to in-memory."""
        denylist = RedisTokenDenylist(redis_client=None)

        await denylist.add_token("fallback_token")
        assert await denylist.is_denylisted("fallback_token") is True

    @pytest.mark.asyncio
    async def test_get_denylist_size_counts_keys(self):
        """get_denylist_size counts matching Redis keys."""
        redis = self._make_redis_mock()
        denylist = RedisTokenDenylist(redis_client=redis)

        size = await denylist.get_denylist_size()
        assert size == 2  # matches the two keys in the mock


# ===========================================================================
# EnhancedBearerTokenStrategy – full flow
# ===========================================================================


class TestEnhancedBearerTokenStrategyIntegration:
    """Integration tests for EnhancedBearerTokenStrategy."""

    @pytest.mark.asyncio
    async def test_valid_token_authenticates(self):
        """A freshly issued token authenticates successfully."""
        strategy = _make_strategy()
        token = strategy._create_access_token("alice", ["admin"], ["read", "write"])
        context = _make_context(token)

        result = await strategy.authenticate(context)

        assert result.is_authenticated
        assert result.user_id == "alice"
        assert "admin" in result.user_roles
        assert "read" in result.permissions

    @pytest.mark.asyncio
    async def test_missing_authorization_header_fails(self):
        """Request without Authorization header is rejected."""
        strategy = _make_strategy()
        context = AuthContext(
            method="GET",
            path="/api/v1/machines",
            headers={},
            query_params={},
            client_ip="10.0.0.1",
        )

        result = await strategy.authenticate(context)

        assert not result.is_authenticated
        assert result.status == AuthStatus.FAILED

    @pytest.mark.asyncio
    async def test_non_bearer_scheme_fails(self):
        """Authorization header with non-Bearer scheme is rejected."""
        strategy = _make_strategy()
        context = AuthContext(
            method="GET",
            path="/api/v1/machines",
            headers={"authorization": "Basic dXNlcjpwYXNz"},
            query_params={},
            client_ip="10.0.0.1",
        )

        result = await strategy.authenticate(context)

        assert not result.is_authenticated

    @pytest.mark.asyncio
    async def test_denylisted_token_rejected_via_authenticate(self):
        """authenticate rejects a token that has been revoked."""
        denylist = InMemoryTokenDenylist()
        strategy = _make_strategy(denylist)

        token = strategy._create_access_token("bob", ["user"], ["read"])
        await strategy.revoke_token(token)

        context = _make_context(token)
        result = await strategy.authenticate(context)

        assert not result.is_authenticated
        assert result.status == AuthStatus.INVALID

    @pytest.mark.asyncio
    async def test_expired_token_returns_expired_status(self):
        """An expired JWT returns AuthStatus.EXPIRED."""
        strategy = _make_strategy(token_expiry=1)
        token = strategy._create_access_token("carol", ["user"], ["read"])

        time.sleep(2)

        result = await strategy.validate_token(token)

        assert not result.is_authenticated
        assert result.status == AuthStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_wrong_signature_rejected(self):
        """Token signed with a different secret is rejected."""
        strategy = _make_strategy()
        other_token = jwt.encode(
            {
                "sub": "attacker",
                "roles": ["admin"],
                "permissions": ["read", "write"],
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            "wrong-secret-key-that-is-long-enough!!",
            algorithm="HS256",
        )

        result = await strategy.validate_token(other_token)

        assert not result.is_authenticated
        assert result.status == AuthStatus.INVALID

    @pytest.mark.asyncio
    async def test_token_missing_sub_claim_rejected(self):
        """Token without 'sub' claim is rejected."""
        strategy = _make_strategy()
        bad_token = jwt.encode(
            {
                "roles": ["user"],
                "permissions": ["read"],
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            SECRET,
            algorithm="HS256",
        )

        result = await strategy.validate_token(bad_token)

        assert not result.is_authenticated

    @pytest.mark.asyncio
    async def test_refresh_token_issues_new_access_token(self):
        """A valid refresh token produces a new access token."""
        strategy = _make_strategy()
        refresh = strategy.create_refresh_token("dave", ["user"], ["read"])

        result = await strategy.refresh_token(refresh)

        assert result.is_authenticated
        assert result.user_id == "dave"
        assert result.token is not None
        assert result.metadata.get("refreshed") is True

    @pytest.mark.asyncio
    async def test_access_token_cannot_be_used_as_refresh_token(self):
        """An access token must not be accepted as a refresh token."""
        strategy = _make_strategy()
        access = strategy._create_access_token("eve", ["user"], ["read"])

        result = await strategy.refresh_token(access)

        assert not result.is_authenticated

    @pytest.mark.asyncio
    async def test_revoked_refresh_token_rejected(self):
        """A revoked refresh token cannot be used to get a new access token."""
        denylist = InMemoryTokenDenylist()
        strategy = _make_strategy(denylist)

        refresh = strategy.create_refresh_token("frank", ["user"], ["read"])
        await denylist.add_token(refresh)

        result = await strategy.refresh_token(refresh)

        assert not result.is_authenticated
        assert result.status == AuthStatus.INVALID

    @pytest.mark.asyncio
    async def test_token_with_invalid_characters_rejected(self):
        """Token containing characters outside the JWT alphabet is rejected."""
        strategy = _make_strategy()
        context = AuthContext(
            method="GET",
            path="/api/v1/machines",
            headers={"authorization": "Bearer bad<>token"},
            query_params={},
            client_ip="10.0.0.1",
        )

        result = await strategy.authenticate(context)

        assert not result.is_authenticated
        assert result.status == AuthStatus.INVALID


# ===========================================================================
# Rate Limiter
# ===========================================================================


class TestRateLimiterIntegration:
    """Integration tests for the RateLimiter used inside EnhancedBearerTokenStrategy."""

    def test_allows_requests_under_limit(self):
        limiter = RateLimiter(max_attempts=5, window_seconds=60)
        for _ in range(5):
            assert limiter.is_rate_limited("192.168.1.1") is False

    def test_blocks_after_limit_exceeded(self):
        limiter = RateLimiter(max_attempts=3, window_seconds=60)
        for _ in range(3):
            limiter.is_rate_limited("192.168.1.2")

        assert limiter.is_rate_limited("192.168.1.2") is True

    def test_different_ips_tracked_independently(self):
        limiter = RateLimiter(max_attempts=2, window_seconds=60)
        limiter.is_rate_limited("1.1.1.1")
        limiter.is_rate_limited("1.1.1.1")

        # 1.1.1.1 is now at limit; 2.2.2.2 should still be allowed
        assert limiter.is_rate_limited("1.1.1.1") is True
        assert limiter.is_rate_limited("2.2.2.2") is False

    @pytest.mark.asyncio
    async def test_rate_limited_ip_gets_failed_auth_result(self):
        """Strategy returns FAILED when IP is rate-limited."""
        denylist = InMemoryTokenDenylist()
        strategy = EnhancedBearerTokenStrategy(
            secret_key=SECRET,
            denylist=denylist,
            algorithm="HS256",
            enabled=True,
            rate_limit_enabled=True,
            max_attempts=1,
            rate_window=60,
        )

        token = strategy._create_access_token("user", ["user"], ["read"])
        context = _make_context(token, client_ip="9.9.9.9")

        # First call consumes the single allowed attempt
        await strategy.authenticate(context)

        # Second call should be rate-limited
        result = await strategy.authenticate(context)
        assert not result.is_authenticated
        assert result.status == AuthStatus.FAILED


# ===========================================================================
# Input Validation
# ===========================================================================


class TestInputValidatorIntegration:
    """Integration tests for InputValidator covering injection attack prevention."""

    # --- SQL injection ---

    def test_sql_injection_semicolon_blocked(self):
        with pytest.raises(ValidationError, match="dangerous character"):
            InputValidator.sanitize_input("'; DROP TABLE users; --")

    def test_sql_injection_comment_blocked(self):
        # The '--' itself is fine (no dangerous chars), but ';' triggers it first
        with pytest.raises(ValidationError):
            InputValidator.sanitize_input("1; SELECT * FROM secrets")

    def test_sql_injection_union_select_allowed_chars(self):
        # UNION SELECT without dangerous chars passes sanitize but fails alphanumeric
        clean = InputValidator.sanitize_input("UNION SELECT name FROM users")
        assert "UNION" in clean

    def test_sql_injection_pipe_blocked(self):
        with pytest.raises(ValidationError, match="dangerous character"):
            InputValidator.sanitize_input("value | other")

    # --- XSS ---

    def test_xss_script_tag_blocked(self):
        with pytest.raises(ValidationError, match="dangerous character"):
            InputValidator.sanitize_input("<script>alert('xss')</script>")

    def test_xss_angle_bracket_open_blocked(self):
        with pytest.raises(ValidationError, match="dangerous character"):
            InputValidator.sanitize_input("<img src=x onerror=alert(1)>")

    def test_xss_ampersand_entity_blocked(self):
        with pytest.raises(ValidationError, match="dangerous character"):
            InputValidator.sanitize_input("&lt;script&gt;")

    def test_xss_event_handler_blocked(self):
        with pytest.raises(ValidationError, match="dangerous character"):
            InputValidator.sanitize_input('"><svg onload=alert(1)>')

    # --- Path traversal ---

    def test_path_traversal_dotdot_slash_blocked(self):
        # Contains no dangerous chars by itself, but validate_alphanumeric catches it
        with pytest.raises(ValidationError):
            InputValidator.validate_alphanumeric("../../etc/passwd")

    def test_path_traversal_null_byte_blocked(self):
        # Null byte is not in DANGEROUS_CHARS but exceeds safe alphanumeric
        with pytest.raises(ValidationError):
            InputValidator.validate_alphanumeric("file\x00.txt")

    def test_path_traversal_encoded_slash_blocked(self):
        with pytest.raises(ValidationError):
            InputValidator.validate_alphanumeric("..%2F..%2Fetc%2Fpasswd")

    # --- Command injection ---

    def test_command_injection_backtick_blocked(self):
        with pytest.raises(ValidationError, match="dangerous character"):
            InputValidator.sanitize_input("`id`")

    def test_command_injection_dollar_blocked(self):
        with pytest.raises(ValidationError, match="dangerous character"):
            InputValidator.sanitize_input("$(cat /etc/passwd)")

    def test_command_injection_newline_blocked(self):
        with pytest.raises(ValidationError, match="dangerous character"):
            InputValidator.sanitize_input("value\nrm -rf /")

    def test_command_injection_curly_braces_blocked(self):
        with pytest.raises(ValidationError, match="dangerous character"):
            InputValidator.sanitize_input("${IFS}cat${IFS}/etc/passwd")

    # --- Length validation ---

    def test_oversized_input_blocked(self):
        with pytest.raises(ValidationError, match="maximum length"):
            InputValidator.sanitize_input("A" * 1001, max_length=1000)

    def test_input_at_max_length_allowed(self):
        value = "A" * 100
        result = InputValidator.sanitize_input(value, max_length=100)
        assert result == value

    def test_input_below_min_length_blocked(self):
        with pytest.raises(ValidationError, match="at least"):
            InputValidator.validate_length("ab", min_length=5)

    # --- Alphanumeric validation ---

    def test_alphanumeric_clean_input_passes(self):
        result = InputValidator.validate_alphanumeric("HelloWorld123")
        assert result == "HelloWorld123"

    def test_alphanumeric_with_dash_allowed_when_flag_set(self):
        result = InputValidator.validate_alphanumeric("my-resource_name", allow_dash=True)
        assert result == "my-resource_name"

    def test_alphanumeric_special_chars_blocked(self):
        with pytest.raises(ValidationError):
            InputValidator.validate_alphanumeric("hello world")

    # --- AWS region validation ---

    def test_valid_aws_region_passes(self):
        from orb.providers.aws.validation.region_validator import validate_aws_region

        assert validate_aws_region("us-east-1") == "us-east-1"
        assert validate_aws_region("eu-west-2") == "eu-west-2"
        assert validate_aws_region("ap-southeast-1") == "ap-southeast-1"

    def test_invalid_aws_region_blocked(self):
        from orb.providers.aws.validation.region_validator import validate_aws_region

        with pytest.raises(ValidationError, match="Invalid AWS region"):
            validate_aws_region("not-a-region")

    def test_aws_region_injection_attempt_blocked(self):
        from orb.providers.aws.validation.region_validator import validate_aws_region

        with pytest.raises(ValidationError):
            validate_aws_region("us-east-1; DROP TABLE")

    # --- Integer validation ---

    def test_valid_integer_passes(self):
        assert InputValidator.validate_integer("42") == 42

    def test_integer_below_min_blocked(self):
        with pytest.raises(ValidationError, match="at least"):
            InputValidator.validate_integer("0", min_value=1)

    def test_integer_above_max_blocked(self):
        with pytest.raises(ValidationError, match="at most"):
            InputValidator.validate_integer("1000", max_value=100)

    def test_non_integer_string_blocked(self):
        with pytest.raises(ValidationError, match="valid integer"):
            InputValidator.validate_integer("abc")

    # --- Choice validation ---

    def test_valid_choice_passes(self):
        result = InputValidator.validate_choice("aws", ["aws", "provider1", "provider2"])
        assert result == "aws"

    def test_invalid_choice_blocked(self):
        with pytest.raises(ValidationError, match="must be one of"):
            InputValidator.validate_choice("unknown", ["aws", "provider1"])

    def test_choice_case_insensitive_by_default(self):
        result = InputValidator.validate_choice("AWS", ["aws", "provider1"])
        assert result == "aws"

    # --- Non-string input ---

    def test_non_string_input_blocked(self):
        with pytest.raises(ValidationError, match="must be a string"):
            InputValidator.sanitize_input(123)  # type: ignore[arg-type]


# ===========================================================================
# SecurityHeadersMiddleware – security headers
# ===========================================================================


class TestSecurityHeadersMiddleware:
    """Integration tests for headers added by SecurityHeadersMiddleware."""

    def _make_app(self, require_https: bool = False):
        """Build a minimal FastAPI app with SecurityHeadersMiddleware."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from orb.api.middleware.security_headers_middleware import SecurityHeadersMiddleware

        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {"ok": True}

        app.add_middleware(SecurityHeadersMiddleware, require_https=require_https)
        return TestClient(app)

    def test_x_frame_options_deny(self):
        client = self._make_app()
        response = client.get("/ping")
        assert response.headers.get("x-frame-options") == "DENY"

    def test_x_content_type_options_nosniff(self):
        client = self._make_app()
        response = client.get("/ping")
        assert response.headers.get("x-content-type-options") == "nosniff"

    def test_x_xss_protection_absent(self):
        """X-XSS-Protection is intentionally omitted (deprecated)."""
        client = self._make_app()
        response = client.get("/ping")
        assert "x-xss-protection" not in response.headers

    def test_strict_transport_security_absent_without_https(self):
        """HSTS must NOT be emitted when require_https is False."""
        client = self._make_app(require_https=False)
        response = client.get("/ping")
        assert "strict-transport-security" not in response.headers

    def test_strict_transport_security_present_with_https(self):
        """HSTS is emitted when require_https=True."""
        client = self._make_app(require_https=True)
        response = client.get("/ping")
        hsts = response.headers.get("strict-transport-security", "")
        assert "max-age=31536000" in hsts
        assert "includeSubDomains" in hsts

    def test_content_security_policy_present(self):
        client = self._make_app()
        response = client.get("/ping")
        csp = response.headers.get("content-security-policy", "")
        assert "default-src" in csp
        assert "frame-ancestors 'none'" in csp

    def test_referrer_policy(self):
        client = self._make_app()
        response = client.get("/ping")
        assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy(self):
        client = self._make_app()
        response = client.get("/ping")
        pp = response.headers.get("permissions-policy", "")
        assert "geolocation=()" in pp
        assert "camera=()" in pp

    def test_required_security_headers_present(self):
        """All required security headers must be present on every response."""
        client = self._make_app()
        response = client.get("/ping")
        expected = [
            "x-frame-options",
            "x-content-type-options",
            "content-security-policy",
            "referrer-policy",
            "permissions-policy",
        ]
        for header in expected:
            assert header in response.headers, f"Missing security header: {header}"

    def test_path_normalization_prevents_traversal(self):
        """Normalized paths must not allow traversal to bypass excluded paths."""
        from unittest.mock import MagicMock

        from orb.api.middleware.auth_middleware import AuthMiddleware

        middleware = AuthMiddleware(
            app=MagicMock(),
            auth_port=MagicMock(),
            excluded_paths=["/health"],
        )

        # These traversal attempts must NOT resolve to /health
        traversal_paths = [
            "/health/../admin",
            "/health/../../etc",
            "/api/../health/../admin",
        ]
        for path in traversal_paths:
            normalized = middleware._normalize_path(path)
            assert normalized != "/health", (
                f"Path traversal '{path}' resolved to excluded path /health"
            )

    def test_excluded_path_exact_match_only(self):
        """Prefix paths must not bypass auth via excluded path matching."""
        from unittest.mock import MagicMock

        from orb.api.middleware.auth_middleware import AuthMiddleware

        middleware = AuthMiddleware(
            app=MagicMock(),
            auth_port=MagicMock(),
            excluded_paths=["/health"],
        )

        assert middleware._is_excluded_path("/health") is True
        assert middleware._is_excluded_path("/healthz") is False
        assert middleware._is_excluded_path("/health/extra") is False
        assert middleware._is_excluded_path("/admin") is False
