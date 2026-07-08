"""Unit tests for server_schema configuration models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# BearerTokenAuthSubConfig — algorithm allowlist validator
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bearer_token_config_default_algorithm_is_hs256():
    """Default algorithm is HS256."""
    from orb.config.schemas.server_schema import BearerTokenAuthSubConfig

    cfg = BearerTokenAuthSubConfig(secret_key="a" * 32)  # type: ignore[call-arg]  # pydantic default fields
    assert cfg.algorithm == "HS256"


@pytest.mark.unit
@pytest.mark.parametrize("algo", ["HS256", "HS384", "HS512"])
def test_bearer_token_config_valid_algorithms(algo: str):
    """HS256, HS384, and HS512 are all accepted."""
    from orb.config.schemas.server_schema import BearerTokenAuthSubConfig

    cfg = BearerTokenAuthSubConfig(secret_key="a" * 32, algorithm=algo)  # type: ignore[call-arg]  # pydantic default fields
    assert cfg.algorithm == algo


@pytest.mark.unit
@pytest.mark.parametrize("algo", ["none", "None", "NONE"])
def test_bearer_token_config_rejects_none_algorithm(algo: str):
    """algorithm='none' (any casing) raises ValidationError with a descriptive message."""
    from orb.config.schemas.server_schema import BearerTokenAuthSubConfig

    with pytest.raises(ValidationError) as exc_info:
        BearerTokenAuthSubConfig(secret_key="a" * 32, algorithm=algo)  # type: ignore[call-arg]  # pydantic default fields

    errors = exc_info.value.errors()
    assert any("none" in str(e).lower() for e in errors)


@pytest.mark.unit
@pytest.mark.parametrize("algo", ["RS256", "ES256", "HS1", "unknown"])
def test_bearer_token_config_rejects_unsupported_algorithms(algo: str):
    """Non-HMAC and unknown algorithms raise ValidationError."""
    from orb.config.schemas.server_schema import BearerTokenAuthSubConfig

    with pytest.raises(ValidationError):
        BearerTokenAuthSubConfig(secret_key="a" * 32, algorithm=algo)  # type: ignore[call-arg]  # pydantic default fields


# ---------------------------------------------------------------------------
# AuthConfig — fail-closed: enabled=True + strategy="none" is rejected
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_auth_config_bare_construction_raises():
    """AuthConfig() (enabled=True, strategy='none') must be rejected.

    The 'none' strategy is a pass-through that grants every anonymous caller
    permissions=['*'].  Combining it with enabled=True is a silent fail-open:
    the server appears authenticated while enforcing nothing.  Construction
    must raise so the misconfiguration surfaces at build time.
    """
    from orb.config.schemas.server_schema import AuthConfig

    with pytest.raises(ValidationError) as exc_info:
        AuthConfig()  # type: ignore[call-arg]

    errors = exc_info.value.errors()
    assert any(
        "pass-through" in str(e).lower() or "real authentication" in str(e).lower() for e in errors
    )


@pytest.mark.unit
def test_auth_config_enabled_true_with_none_strategy_raises():
    """AuthConfig(enabled=True, strategy='none') must be rejected."""
    from orb.config.schemas.server_schema import AuthConfig

    with pytest.raises(ValidationError):
        AuthConfig(enabled=True, strategy="none")  # type: ignore[call-arg]


@pytest.mark.unit
def test_auth_config_enabled_true_with_empty_strategy_raises():
    """AuthConfig(enabled=True, strategy='') must be rejected."""
    from orb.config.schemas.server_schema import AuthConfig

    with pytest.raises(ValidationError):
        AuthConfig(enabled=True, strategy="")  # type: ignore[call-arg]


@pytest.mark.unit
def test_auth_config_explicit_false_preserved():
    """Callers that pass enabled=False explicitly keep their intended behavior."""
    from orb.config.schemas.server_schema import AuthConfig

    cfg = AuthConfig(enabled=False)  # type: ignore[call-arg]
    assert cfg.enabled is False


@pytest.mark.unit
def test_auth_config_enabled_true_with_real_strategy_accepted():
    """AuthConfig(enabled=True) is accepted when a real strategy is specified."""
    from orb.config.schemas.server_schema import AuthConfig

    for strategy in ("bearer_token", "bearer_token_enhanced", "iam", "cognito"):
        cfg = AuthConfig(enabled=True, strategy=strategy)  # type: ignore[call-arg]
        assert cfg.enabled is True
        assert cfg.strategy == strategy


# ---------------------------------------------------------------------------
# CORSConfig — secure defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cors_config_default_origins_is_loopback_only():
    """Default CORS origins must NOT be the wildcard '*'."""
    from orb.config.schemas.server_schema import CORSConfig

    cfg = CORSConfig()
    assert cfg.origins != ["*"], (
        "Default cors.origins must not be ['*']. "
        "Operators binding to 0.0.0.0 must set this explicitly."
    )
    # Default should allow only loopback.
    assert all("localhost" in o or "127.0.0.1" in o for o in cfg.origins), (
        f"Default cors.origins should be loopback-only, got: {cfg.origins}"
    )


@pytest.mark.unit
def test_cors_config_wildcard_can_be_set_explicitly():
    """Operators CAN still override origins to ['*'] explicitly when they need it."""
    from orb.config.schemas.server_schema import CORSConfig

    cfg = CORSConfig(origins=["*"])  # type: ignore[call-arg]
    assert cfg.origins == ["*"]


# ---------------------------------------------------------------------------
# CORSConfig — credentials + wildcard origin is rejected
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cors_credentials_with_wildcard_origin_raises():
    """credentials=True + origins=['*'] is an insecure combo and must be rejected."""
    from orb.config.schemas.server_schema import CORSConfig

    with pytest.raises(ValidationError) as exc_info:
        CORSConfig(credentials=True, origins=["*"])  # type: ignore[call-arg]

    errors = exc_info.value.errors()
    assert any("credentials" in str(e).lower() for e in errors)


@pytest.mark.unit
def test_cors_credentials_false_with_wildcard_origin_is_allowed():
    """credentials=False (the default) with origins=['*'] is permitted."""
    from orb.config.schemas.server_schema import CORSConfig

    cfg = CORSConfig(credentials=False, origins=["*"])  # type: ignore[call-arg]
    assert cfg.credentials is False
    assert "*" in cfg.origins


@pytest.mark.unit
def test_cors_credentials_true_with_explicit_origin_is_allowed():
    """credentials=True with explicit (non-wildcard) origins and no wildcard headers is permitted."""
    from orb.config.schemas.server_schema import CORSConfig

    cfg = CORSConfig(  # type: ignore[call-arg]
        credentials=True,
        origins=["https://app.example.com"],
        headers=["Authorization", "Content-Type"],
    )
    assert cfg.credentials is True
    assert cfg.origins == ["https://app.example.com"]


@pytest.mark.unit
def test_cors_credentials_true_with_mixed_origins_raises():
    """credentials=True raises even when '*' appears alongside explicit origins."""
    from orb.config.schemas.server_schema import CORSConfig

    with pytest.raises(ValidationError):
        CORSConfig(credentials=True, origins=["https://app.example.com", "*"])  # type: ignore[call-arg]


@pytest.mark.unit
def test_cors_default_credentials_is_false():
    """CORSConfig defaults to credentials=False."""
    from orb.config.schemas.server_schema import CORSConfig

    cfg = CORSConfig()  # type: ignore[call-arg]
    assert cfg.credentials is False


@pytest.mark.unit
def test_cors_credentials_whitespace_padded_wildcard_raises():
    """credentials=True with a whitespace-padded '*' entry is rejected."""
    from orb.config.schemas.server_schema import CORSConfig

    with pytest.raises(ValidationError):
        CORSConfig(credentials=True, origins=[" * "])  # type: ignore[call-arg]


@pytest.mark.unit
def test_cors_credentials_subdomain_wildcard_raises():
    """credentials=True with a subdomain wildcard (https://*.example.com) is rejected."""
    from orb.config.schemas.server_schema import CORSConfig

    with pytest.raises(ValidationError):
        CORSConfig(credentials=True, origins=["https://*.example.com"])  # type: ignore[call-arg]


@pytest.mark.unit
def test_cors_credentials_with_headers_wildcard_raises():
    """credentials=True + headers=['*'] is rejected even with explicit origins."""
    from orb.config.schemas.server_schema import CORSConfig

    with pytest.raises(ValidationError):
        CORSConfig(  # type: ignore[call-arg]
            credentials=True,
            origins=["https://app.example.com"],
            headers=["*"],
        )


@pytest.mark.unit
def test_cors_credentials_false_with_headers_wildcard_allowed():
    """credentials=False with headers=['*'] is permitted (the default)."""
    from orb.config.schemas.server_schema import CORSConfig

    cfg = CORSConfig(credentials=False, origins=["*"], headers=["*"])  # type: ignore[call-arg]
    assert cfg.credentials is False


# ---------------------------------------------------------------------------
# check_destructive_admin_allowed — anonymous cannot pass with strategy="none"
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_destructive_admin_blocked_for_no_strategy(monkeypatch):
    """check_destructive_admin_allowed must refuse when auth strategy is 'none'.

    Even if auth.enabled=True is set in config, the 'none' strategy grants
    every anonymous caller permissions=['*'].  The guard must detect this and
    return 403 AUTH_STRATEGY_NONE rather than allowing the destructive action.
    """
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from orb.api.dependencies import check_destructive_admin_allowed

    # Cannot construct AuthConfig(enabled=True, strategy="none") because the
    # validator rejects it.  Use a MagicMock to simulate a misconfigured or
    # bypassed config object that presents this invalid state at runtime.
    mock_auth = MagicMock()
    mock_auth.enabled = True
    mock_auth.strategy = "none"

    mock_server_cfg = MagicMock()
    mock_server_cfg.auth = mock_auth

    mock_request = MagicMock()
    monkeypatch.setattr(
        "orb.api.dependencies.get_server_config",
        lambda: mock_server_cfg,
    )

    with pytest.raises(HTTPException) as exc_info:
        check_destructive_admin_allowed(mock_request)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "AUTH_STRATEGY_NONE"


@pytest.mark.unit
def test_destructive_admin_blocked_when_auth_disabled(monkeypatch):
    """check_destructive_admin_allowed must refuse when auth.enabled=False."""
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from orb.api.dependencies import check_destructive_admin_allowed

    mock_auth = MagicMock()
    mock_auth.enabled = False
    mock_auth.strategy = "bearer_token"

    mock_server_cfg = MagicMock()
    mock_server_cfg.auth = mock_auth

    mock_request = MagicMock()
    monkeypatch.setattr(
        "orb.api.dependencies.get_server_config",
        lambda: mock_server_cfg,
    )

    with pytest.raises(HTTPException) as exc_info:
        check_destructive_admin_allowed(mock_request)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "AUTH_DISABLED"


# ---------------------------------------------------------------------------
# ServerConfig — trusted_hosts secure defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_server_config_default_trusted_hosts_is_not_wildcard():
    """Default trusted_hosts must NOT be the wildcard '*'."""
    from orb.config.schemas.server_schema import ServerConfig

    cfg = ServerConfig()
    assert cfg.trusted_hosts != ["*"], (
        "Default trusted_hosts must not be ['*']. "
        "Operators binding to 0.0.0.0 must add their hostname explicitly."
    )


@pytest.mark.unit
def test_server_config_default_trusted_hosts_contains_loopback():
    """Default trusted_hosts must include loopback addresses for local development."""
    from orb.config.schemas.server_schema import ServerConfig

    cfg = ServerConfig()
    assert "localhost" in cfg.trusted_hosts
    assert "127.0.0.1" in cfg.trusted_hosts


@pytest.mark.unit
def test_server_config_default_trusted_hosts_contains_testserver():
    """Default trusted_hosts must include 'testserver' for Starlette TestClient."""
    from orb.config.schemas.server_schema import ServerConfig

    cfg = ServerConfig()
    assert "testserver" in cfg.trusted_hosts


@pytest.mark.unit
def test_server_config_trusted_hosts_can_be_overridden():
    """Operators can override trusted_hosts to any list they need."""
    from orb.config.schemas.server_schema import ServerConfig

    cfg = ServerConfig(trusted_hosts=["myapp.example.com", "localhost"])  # type: ignore[call-arg]
    # Exact list comparison (not substring membership) — asserts the operator
    # override is preserved verbatim and avoids CodeQL's URL-substring heuristic.
    assert cfg.trusted_hosts == ["myapp.example.com", "localhost"]


# ---------------------------------------------------------------------------
# IAMAuthSubConfig — admin_arns field
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_iam_auth_sub_config_admin_arns_defaults_empty():
    """admin_arns defaults to an empty list when not specified."""
    from orb.config.schemas.server_schema import IAMAuthSubConfig

    cfg = IAMAuthSubConfig()
    assert cfg.admin_arns == []


@pytest.mark.unit
def test_iam_auth_sub_config_admin_arns_accepts_full_arns():
    """admin_arns accepts a list of fully-qualified ARN strings."""
    from orb.config.schemas.server_schema import IAMAuthSubConfig

    arns = [
        "arn:aws:iam::123456789012:role/OrbAdmin",
        "arn:aws:iam::123456789012:user/admin-user",
    ]
    cfg = IAMAuthSubConfig(admin_arns=arns)  # type: ignore[call-arg]
    assert cfg.admin_arns == arns
