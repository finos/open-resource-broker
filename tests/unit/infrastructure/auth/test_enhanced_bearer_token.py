"""Unit tests for EnhancedBearerTokenStrategy."""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest

from orb.infrastructure.adapters.ports.auth import AuthContext, AuthStatus
from orb.infrastructure.auth.strategy.bearer_token_strategy_enhanced import (
    EnhancedBearerTokenStrategy,
)
from orb.infrastructure.auth.token_denylist.in_memory_denylist import InMemoryTokenDenylist

_SECRET = "a" * 32  # exactly 32 bytes — minimum valid length


def _make_denylist() -> InMemoryTokenDenylist:
    return InMemoryTokenDenylist()


def _make_strategy(
    denylist: InMemoryTokenDenylist | None = None,
    rate_limit_enabled: bool = False,
) -> EnhancedBearerTokenStrategy:
    return EnhancedBearerTokenStrategy(
        secret_key=_SECRET,
        denylist=denylist or _make_denylist(),
        rate_limit_enabled=rate_limit_enabled,
    )


def _make_context(**kwargs) -> AuthContext:
    defaults: dict[str, Any] = {
        "method": "GET",
        "path": "/api/v1/machines",
        "headers": {},
        "query_params": {},
        "client_ip": "127.0.0.1",
    }
    defaults.update(kwargs)
    return AuthContext(**defaults)


def _make_token(
    user_id: str = "user-1",
    roles: list[str] | None = None,
    expiry_offset: int = 3600,
) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "roles": roles or ["user"],
        "permissions": [],
        "type": "access",
        "iat": now,
        "exp": now + expiry_offset,
        "iss": "open-resource-broker",
    }
    return jwt.encode(payload, _SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# authenticate() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_enhanced_bearer_authenticate_success():
    """authenticate() returns SUCCESS for a valid Bearer token."""
    strategy = _make_strategy()
    token = _make_token()

    result = await strategy.authenticate(
        _make_context(headers={"authorization": f"Bearer {token}"})
    )

    assert result.status == AuthStatus.SUCCESS
    assert result.user_id == "user-1"


# ---------------------------------------------------------------------------
# authenticate() — missing header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_enhanced_bearer_authenticate_missing_header():
    """authenticate() returns FAILED when Authorization header is absent."""
    strategy = _make_strategy()

    result = await strategy.authenticate(_make_context(headers={}))

    assert result.status == AuthStatus.FAILED


# ---------------------------------------------------------------------------
# authenticate() — denylisted token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_enhanced_bearer_authenticate_denylisted_token():
    """validate_token() returns INVALID for a denylisted token."""
    denylist = _make_denylist()
    strategy = _make_strategy(denylist=denylist)
    token = _make_token()

    await denylist.add_token(token)
    result = await strategy.validate_token(token)

    assert result.status == AuthStatus.INVALID
    assert "revoked" in (result.error_message or "").lower()


# ---------------------------------------------------------------------------
# authenticate() — rate limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_enhanced_bearer_rate_limit():
    """authenticate() returns FAILED when rate limit is exceeded."""
    strategy = EnhancedBearerTokenStrategy(
        secret_key=_SECRET,
        denylist=_make_denylist(),
        rate_limit_enabled=True,
        max_attempts=1,
        rate_window=60,
    )

    ctx = _make_context(headers={"authorization": "Bearer invalid"})

    # First attempt consumes the single allowed slot (will fail on bad token, not rate limit)
    await strategy.authenticate(ctx)

    # Second attempt should hit the rate limit
    result = await strategy.authenticate(ctx)
    assert result.status == AuthStatus.FAILED
    assert "too many" in (result.error_message or "").lower()


# ---------------------------------------------------------------------------
# revoke_token()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_enhanced_bearer_revoke_token():
    """revoke_token() adds the token to the denylist."""
    denylist = _make_denylist()
    strategy = _make_strategy(denylist=denylist)
    token = _make_token()

    success = await strategy.revoke_token(token)
    assert success is True
    assert await denylist.is_denylisted(token) is True


# ---------------------------------------------------------------------------
# validate_token() — invalid signature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_enhanced_bearer_invalid_signature():
    """validate_token() returns INVALID for a token signed with a different key."""
    strategy = _make_strategy()
    wrong_token = jwt.encode(
        {"sub": "x", "iat": int(time.time()), "exp": int(time.time()) + 60},
        "b" * 32,
        algorithm="HS256",
    )

    result = await strategy.validate_token(wrong_token)

    assert result.status == AuthStatus.INVALID


# ---------------------------------------------------------------------------
# from_auth_config classmethod
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enhanced_bearer_from_auth_config():
    """from_auth_config builds strategy from typed BearerTokenAuthSubConfig."""
    from orb.config.schemas.server_schema import AuthConfig, BearerTokenAuthSubConfig

    auth_config = AuthConfig(
        strategy="bearer_token_enhanced",
        bearer_token=BearerTokenAuthSubConfig(secret_key=_SECRET),
    )

    strategy = EnhancedBearerTokenStrategy.from_auth_config(auth_config)

    assert strategy.secret_key == _SECRET
    assert strategy.enabled is True


@pytest.mark.unit
def test_enhanced_bearer_from_auth_config_missing_bearer_token():
    """from_auth_config raises ConfigurationError when bearer_token sub-config is absent."""
    from orb.config.schemas.server_schema import AuthConfig
    from orb.domain.base.exceptions import ConfigurationError

    auth_config = AuthConfig(strategy="bearer_token_enhanced")

    with pytest.raises(ConfigurationError):
        EnhancedBearerTokenStrategy.from_auth_config(auth_config)


# ---------------------------------------------------------------------------
# Short secret key → ConfigurationError (not a warning)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enhanced_bearer_short_key_raises_configuration_error():
    """
    EnhancedBearerTokenStrategy must raise ConfigurationError for keys shorter
    than 32 bytes, matching the error policy of BearerTokenStrategy.
    """
    from orb.domain.base.exceptions import ConfigurationError

    short_key = "a" * 16  # 16 bytes — below the 32-byte minimum

    with pytest.raises(ConfigurationError, match="32 bytes"):
        EnhancedBearerTokenStrategy(
            secret_key=short_key,
            denylist=_make_denylist(),
        )
