"""Tests for RateLimitMiddleware — burst config, trusted-proxy IP, token bucket."""

from unittest.mock import MagicMock

import pytest

from orb.api.middleware.rate_limit_middleware import RateLimitMiddleware


class TestRateLimitConfig:
    """Constructor correctly reads burst and requests_per_minute from dict and typed config."""

    def test_dict_config_with_burst(self):
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {}

        mw = RateLimitMiddleware(
            app,
            rate_limiting_config={
                "enabled": True,
                "requests_per_minute": 300,
                "burst": 60,
                "max_buckets": 5000,
            },
        )
        assert mw._capacity == 300.0
        assert mw._burst == 60.0
        assert mw._max_buckets == 5000
        assert mw._enabled is True

    def test_dict_config_burst_defaults_to_rpm(self):
        """When 'burst' is absent in a dict config, it defaults to requests_per_minute."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {}

        mw = RateLimitMiddleware(
            app,
            rate_limiting_config={"enabled": True, "requests_per_minute": 120},
        )
        assert mw._capacity == 120.0
        assert mw._burst == 120.0

    def test_typed_config(self):
        """RateLimitConfig typed object is read correctly."""
        from fastapi import FastAPI

        from orb.config.schemas.server_schema import RateLimitConfig

        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {}

        cfg = RateLimitConfig(enabled=True, requests_per_minute=300, burst=60)
        mw = RateLimitMiddleware(app, rate_limiting_config=cfg)
        assert mw._capacity == 300.0
        assert mw._burst == 60.0
        assert mw._enabled is True

    def test_none_config_disables_limiter(self):
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {}

        mw = RateLimitMiddleware(app, rate_limiting_config=None)
        assert mw._enabled is False

    def test_trusted_proxies_stored(self):
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {}

        mw = RateLimitMiddleware(
            app,
            rate_limiting_config={"enabled": True, "requests_per_minute": 100},
            trusted_proxies=["10.0.0.1", "10.0.0.2"],
        )
        assert mw._trusted_proxies == frozenset({"10.0.0.1", "10.0.0.2"})


class TestRateLimitIdentityResolution:
    """_resolve_identity uses shared helper for IP, respecting trusted proxies."""

    def _make_mw(self, trusted_proxies=None) -> RateLimitMiddleware:
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {}

        return RateLimitMiddleware(
            app,
            rate_limiting_config={"enabled": True, "requests_per_minute": 300, "burst": 60},
            trusted_proxies=trusted_proxies or [],
        )

    def test_anonymous_uses_direct_ip_without_proxies(self):
        mw = self._make_mw()
        request = MagicMock()
        request.state = MagicMock(spec=[])  # no user_id attribute
        request.client.host = "4.4.4.4"

        identity = mw._resolve_identity(request)
        assert identity == "ip:4.4.4.4"

    def test_anonymous_uses_forwarded_ip_behind_trusted_proxy(self):
        mw = self._make_mw(trusted_proxies=["10.0.0.1"])
        request = MagicMock()
        request.state = MagicMock(spec=[])
        request.client.host = "10.0.0.1"

        headers_map = {"x-forwarded-for": "203.0.113.5"}
        request.headers.get = lambda k, d=None: headers_map.get(k, d)

        identity = mw._resolve_identity(request)
        assert identity == "ip:203.0.113.5"

    def test_authenticated_user_uses_user_id(self):
        mw = self._make_mw()
        request = MagicMock()
        request.state.user_id = "alice"
        request.client.host = "1.2.3.4"

        identity = mw._resolve_identity(request)
        assert identity == "user:alice"

    def test_anonymous_string_falls_back_to_ip(self):
        mw = self._make_mw()
        request = MagicMock()
        request.state.user_id = "anonymous"
        request.client.host = "5.6.7.8"
        request.headers.get = lambda k, d=None: None

        identity = mw._resolve_identity(request)
        assert identity == "ip:5.6.7.8"


class TestRateLimitBucketInitialFill:
    """New buckets start at burst capacity, not the full per-minute capacity."""

    @pytest.mark.asyncio
    async def test_new_bucket_starts_at_burst(self):
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {}

        mw = RateLimitMiddleware(
            app,
            rate_limiting_config={"enabled": True, "requests_per_minute": 300, "burst": 5},
        )

        # Consume burst tokens — first 5 should be allowed
        for _ in range(5):
            allowed, _ = await mw._check_and_consume("test-identity")
            assert allowed is True

        # 6th request exceeds burst
        allowed, retry_after = await mw._check_and_consume("test-identity")
        assert allowed is False
        assert retry_after > 0


class TestQueryParamRedaction:
    """Sensitive query params must be scrubbed before appearing in DEBUG logs."""

    def test_sensitive_keys_redacted(self):
        """token, api_key, access_token, password are replaced with [REDACTED]."""
        from orb.api.middleware.logging_middleware import _scrub_query_params

        params = {
            "token": "abc123",
            "api_key": "secret-key",
            "access_token": "bearer-xyz",
            "password": "hunter2",
            "q": "search-term",
            "page": "2",
        }
        scrubbed = _scrub_query_params(params)
        assert scrubbed["token"] == "[REDACTED]"
        assert scrubbed["api_key"] == "[REDACTED]"
        assert scrubbed["access_token"] == "[REDACTED]"
        assert scrubbed["password"] == "[REDACTED]"
        # Non-sensitive params are preserved
        assert scrubbed["q"] == "search-term"
        assert scrubbed["page"] == "2"

    def test_case_insensitive_redaction(self):
        """Key comparison is case-insensitive: Token, TOKEN, token all redacted."""
        from orb.api.middleware.logging_middleware import _scrub_query_params

        params = {"Token": "abc", "PASSWORD": "xyz", "API_KEY": "secret"}
        scrubbed = _scrub_query_params(params)
        assert scrubbed["Token"] == "[REDACTED]"
        assert scrubbed["PASSWORD"] == "[REDACTED]"
        assert scrubbed["API_KEY"] == "[REDACTED]"

    def test_empty_params_unchanged(self):
        """Empty dict passes through without error."""
        from orb.api.middleware.logging_middleware import _scrub_query_params

        assert _scrub_query_params({}) == {}

    def test_original_params_not_mutated(self):
        """The scrubber returns a new dict and does not mutate the original."""
        from orb.api.middleware.logging_middleware import _scrub_query_params

        original = {"token": "abc123", "q": "hello"}
        _scrub_query_params(original)
        # Original must be unchanged
        assert original["token"] == "abc123"

    def test_debug_log_contains_redacted(self, caplog):
        """LoggingMiddleware DEBUG output shows [REDACTED] for sensitive params."""
        import logging

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from orb.api.middleware.logging_middleware import LoggingMiddleware

        app = FastAPI()
        app.add_middleware(LoggingMiddleware, log_requests=True, log_responses=False)

        @app.get("/search")
        def search():
            return {}

        with caplog.at_level(logging.DEBUG, logger="orb.api.middleware.logging_middleware"):
            client = TestClient(app)
            client.get("/search?token=abc123&password=xyz&q=hello")

        debug_logs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
        param_logs = [m for m in debug_logs if "query params" in m]
        assert param_logs, "No query-params DEBUG record found"
        combined = " ".join(param_logs)
        assert "abc123" not in combined, "token value leaked in logs"
        assert "xyz" not in combined, "password value leaked in logs"
        assert "[REDACTED]" in combined, "[REDACTED] sentinel absent from logs"
