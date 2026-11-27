"""Pin the exact middleware stack ``create_fastapi_app`` installs.

Starlette wraps middleware in reverse registration order, so ``app.user_middleware``
lists the stack outermost first.  Reordering, dropping or re-parameterising a
registration silently changes what runs before authentication, so every
conditional branch is asserted here with its security-bearing arguments.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from orb.api.server import _LoopbackAdminAuthWrapper, create_fastapi_app
from orb.config.schemas.server_schema import (
    AuthConfig,
    CORSConfig,
    DocsConfig,
    RateLimitConfig,
    ServerConfig,
)

pytestmark = [pytest.mark.unit, pytest.mark.api]

_SECRET = "test-secret-key-for-middleware-stack-order!"
_PROXIES = ["10.0.0.1"]
_HOSTS = ["orb.example.com"]
_ORIGINS = ["https://ui.example.com"]


def _auth(enabled: bool = True) -> AuthConfig:
    return AuthConfig(  # type: ignore[call-arg]
        enabled=enabled,
        strategy="bearer_token",
        bearer_token={"secret_key": _SECRET, "algorithm": "HS256"},
    )


def _config(tmp_path, **overrides: Any) -> ServerConfig:
    base: dict[str, Any] = {
        "enabled": True,
        "require_https": True,
        "trusted_proxies": _PROXIES,
        "read_only": True,
        "auth": _auth(),
        "rate_limiting": RateLimitConfig(enabled=True, requests_per_minute=60, burst=10),  # type: ignore[call-arg]
        "audit_log_enabled": True,
        "trusted_hosts": _HOSTS,
        "cors": CORSConfig(origins=_ORIGINS),  # type: ignore[call-arg]
        "pid_file": str(tmp_path / "orb-server.pid"),
    }
    base.update(overrides)
    return ServerConfig(**base)  # type: ignore[call-arg]


def _build(config: ServerConfig) -> Any:
    with patch(
        "orb.api.server._register_routers",
        side_effect=lambda app: app.include_router(APIRouter()),
    ):
        return create_fastapi_app(config)


def _names(app: Any) -> list[str]:
    return [m.cls.__name__ for m in app.user_middleware]


def _by_name(app: Any) -> dict[str, dict[str, Any]]:
    return {m.cls.__name__: dict(m.kwargs) for m in app.user_middleware}


@pytest.fixture(autouse=True)
def _isolate_loopback_state():
    saved = (
        set(_LoopbackAdminAuthWrapper._tokens),
        _LoopbackAdminAuthWrapper._token_file,
        _LoopbackAdminAuthWrapper._token_file_mtime,
    )
    _LoopbackAdminAuthWrapper._tokens = set()
    _LoopbackAdminAuthWrapper._token_file = None
    yield
    (
        _LoopbackAdminAuthWrapper._tokens,
        _LoopbackAdminAuthWrapper._token_file,
        _LoopbackAdminAuthWrapper._token_file_mtime,
    ) = saved


_CORS = "CORSMiddleware"
_HOST = "TrustedHostMiddleware"
_AUTH = "AuthMiddleware"
_RATE = "RateLimitMiddleware"
_AUDIT = "AuditLogMiddleware"
_LOOPBACK = "_LoopbackAdminTokenMiddleware"
_LOG = "LoggingMiddleware"
_RO = "ReadOnlyMiddleware"
_FWD = "ForwardedProtoMiddleware"
_HTTPS = "HTTPSRedirectMiddleware"
_SEC = "SecurityHeadersMiddleware"

# Outermost first: CORS answers preflights before anything else, Auth runs before
# RateLimit so the limiter sees the caller's identity, SecurityHeaders is innermost.
FULL_STACK = [_CORS, _HOST, _AUTH, _RATE, _AUDIT, _LOOPBACK, _LOG, _RO, _FWD, _HTTPS, _SEC]


def _without(*names: str) -> list[str]:
    return [n for n in FULL_STACK if n not in names]


_CASES = [
    pytest.param({}, FULL_STACK, id="everything-enabled"),
    pytest.param({"auth": _auth(False)}, _without(_AUTH), id="auth-off"),
    pytest.param({"cors": CORSConfig(enabled=False)}, _without(_CORS), id="cors-off"),  # type: ignore[call-arg]
    pytest.param({"require_https": False}, _without(_FWD, _HTTPS), id="https-off"),
    pytest.param({"trusted_proxies": []}, _without(_FWD), id="https-without-proxies"),
    pytest.param(
        {"rate_limiting": RateLimitConfig(enabled=False)},  # type: ignore[call-arg]
        _without(_RATE),
        id="rate-limit-off",
    ),
    pytest.param({"read_only": False}, _without(_RO), id="read-only-off"),
    pytest.param({"audit_log_enabled": False}, _without(_AUDIT), id="audit-off"),
    pytest.param({"trusted_hosts": []}, _without(_HOST), id="trusted-hosts-empty"),
    pytest.param({"trusted_hosts": ["*"]}, _without(_HOST), id="trusted-hosts-wildcard"),
    pytest.param(
        {
            "auth": _auth(False),
            "cors": CORSConfig(enabled=False),  # type: ignore[call-arg]
            "require_https": False,
            "rate_limiting": RateLimitConfig(enabled=False),  # type: ignore[call-arg]
            "read_only": False,
            "audit_log_enabled": False,
            "trusted_hosts": [],
        },
        [_LOOPBACK, _LOG, _SEC],
        id="minimal",
    ),
]


@pytest.mark.parametrize(("overrides", "expected"), _CASES)
def test_stack_order(tmp_path, overrides: dict[str, Any], expected: list[str]) -> None:
    assert _names(_build(_config(tmp_path, **overrides))) == expected


def test_security_arguments_of_the_full_stack(tmp_path) -> None:
    """Dropping or changing an argument during a refactor must turn this red."""
    config = _config(tmp_path)
    kwargs = _by_name(_build(config))

    assert kwargs[_CORS] == {
        "allow_origins": _ORIGINS,
        "allow_credentials": False,
        "allow_methods": config.cors.methods,
        "allow_headers": config.cors.headers,
        "expose_headers": ["X-ORB-Scheduler"],
    }
    assert kwargs[_HOST] == {"allowed_hosts": _HOSTS}

    auth = dict(kwargs[_AUTH])
    auth_port = auth.pop("auth_port")
    assert isinstance(auth_port, _LoopbackAdminAuthWrapper)
    assert auth == {
        "require_auth": True,
        "excluded_paths": ["/health", "/livez", "/readyz", "/favicon.ico"],
        "trusted_proxies": _PROXIES,
    }

    assert kwargs[_RATE] == {
        "rate_limiting_config": config.rate_limiting,
        "trusted_proxies": _PROXIES,
    }
    assert kwargs[_AUDIT] == {"trusted_proxies": _PROXIES}
    assert kwargs[_LOOPBACK] == {}
    assert kwargs[_LOG] == {}
    assert kwargs[_RO] == {"enabled": True}
    assert kwargs[_FWD] == {"trusted_proxies": _PROXIES}
    assert kwargs[_HTTPS] == {}
    assert kwargs[_SEC] == {"require_https": True, "hsts_max_age": config.hsts_max_age}


def test_public_docs_are_excluded_from_auth_only_when_configured(tmp_path) -> None:
    protected = _by_name(_build(_config(tmp_path)))[_AUTH]["excluded_paths"]
    public = _by_name(_build(_config(tmp_path, docs=DocsConfig(require_auth=False))))[_AUTH][  # type: ignore[call-arg]
        "excluded_paths"
    ]

    assert "/docs" not in protected
    assert public == [
        "/health",
        "/livez",
        "/readyz",
        "/favicon.ico",
        "/docs",
        "/redoc",
        "/openapi.json",
    ]


def test_loopback_token_file_is_loaded_into_the_stack(tmp_path) -> None:
    (tmp_path / "orb-server.token").write_text("loopback-secret")

    _build(_config(tmp_path))

    assert _LoopbackAdminAuthWrapper._tokens == {"loopback-secret"}


def test_no_loopback_token_without_a_token_file(tmp_path) -> None:
    _build(_config(tmp_path))

    assert _LoopbackAdminAuthWrapper._tokens == set()


def test_auth_reaches_the_routes_when_enabled_and_not_when_disabled(tmp_path) -> None:
    """The auth port the stack builds is the one the app enforces."""
    secured = TestClient(
        _build(_config(tmp_path, require_https=False, trusted_hosts=["testserver"])),
        raise_server_exceptions=False,
    )
    assert secured.get("/info").status_code == 401
    assert secured.get("/livez").status_code == 200

    open_ = TestClient(
        _build(
            _config(tmp_path, require_https=False, trusted_hosts=["testserver"], auth=_auth(False))
        ),
        raise_server_exceptions=False,
    )
    assert open_.get("/info").status_code == 200
