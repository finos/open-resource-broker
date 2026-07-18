"""Unit tests for BearerTokenStrategy.

Coverage targets: lines 43,73,81,105,113,136-138,150,152,155,160-162,164,166,
176-177,181-183,222,228,238,242,268,307-308,318
"""

from __future__ import annotations

import time

import jwt
import pytest

from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.adapters.ports.auth import AuthContext, AuthStatus
from orb.infrastructure.auth.strategy.bearer_token_strategy import BearerTokenStrategy

pytestmark = pytest.mark.unit

_SECRET = "a" * 32  # 32 bytes — minimum valid length


def _make_strategy(**kwargs) -> BearerTokenStrategy:
    defaults = {"secret_key": _SECRET, "algorithm": "HS256", "token_expiry": 3600}
    defaults.update(kwargs)
    return BearerTokenStrategy(**defaults)


def _make_context(auth_header: str = "") -> AuthContext:
    return AuthContext(
        method="GET",
        path="/api/v1/test",
        headers={"authorization": auth_header} if auth_header else {},
        query_params={},
        client_ip="127.0.0.1",
    )


def _make_token(
    user_id: str = "user-1",
    exp_offset: int = 3600,
    include_user: bool = True,
    roles: list[str] | None = None,
) -> str:
    now = int(time.time())
    payload: dict = {
        "iat": now,
        "exp": now + exp_offset,
    }
    if include_user:
        payload["sub"] = user_id
    if roles is not None:
        payload["roles"] = roles
    return jwt.encode(payload, _SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestBearerTokenStrategyConstruction:
    def test_short_secret_raises_value_error(self):
        with pytest.raises(ValueError, match="32 bytes"):
            BearerTokenStrategy(secret_key="short")

    def test_valid_secret_constructs_ok(self):
        s = _make_strategy()
        assert s.secret_key == _SECRET

    def test_get_strategy_name(self):
        assert _make_strategy().get_strategy_name() == "bearer_token"

    def test_is_enabled_default_true(self):
        assert _make_strategy().is_enabled() is True

    def test_is_enabled_false_when_set(self):
        s = BearerTokenStrategy(secret_key=_SECRET, enabled=False)
        assert s.is_enabled() is False


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


class TestAuthenticate:
    def test_missing_authorization_header_fails(self):
        s = _make_strategy()
        ctx = _make_context("")
        result = asyncio_run(s.authenticate(ctx))
        assert result.status == AuthStatus.FAILED

    def test_non_bearer_prefix_fails(self):
        s = _make_strategy()
        ctx = _make_context("Basic dXNlcjpwYXNz")
        result = asyncio_run(s.authenticate(ctx))
        assert result.status == AuthStatus.FAILED

    def test_empty_token_after_bearer_prefix_returns_invalid(self):
        s = _make_strategy()
        ctx = _make_context("Bearer ")
        result = asyncio_run(s.authenticate(ctx))
        assert result.status == AuthStatus.INVALID

    def test_token_with_invalid_chars_returns_invalid(self):
        s = _make_strategy()
        # Inject a space which is not in the JWT charset
        ctx = _make_context("Bearer bad token here")
        result = asyncio_run(s.authenticate(ctx))
        assert result.status == AuthStatus.INVALID

    def test_valid_token_returns_success(self):
        s = _make_strategy()
        token = _make_token()
        ctx = _make_context(f"Bearer {token}")
        result = asyncio_run(s.authenticate(ctx))
        assert result.status == AuthStatus.SUCCESS
        assert result.user_id == "user-1"


# ---------------------------------------------------------------------------
# validate_token
# ---------------------------------------------------------------------------


class TestValidateToken:
    def test_valid_token_returns_success_with_user_id(self):
        s = _make_strategy()
        token = _make_token()
        result = asyncio_run(s.validate_token(token))
        assert result.status == AuthStatus.SUCCESS
        assert result.user_id == "user-1"
        assert result.token == token

    def test_expired_token_returns_expired(self):
        s = _make_strategy()
        token = _make_token(exp_offset=-1)
        result = asyncio_run(s.validate_token(token))
        assert result.status == AuthStatus.EXPIRED

    def test_token_without_sub_returns_invalid(self):
        s = _make_strategy()
        token = _make_token(include_user=False)
        result = asyncio_run(s.validate_token(token))
        assert result.status == AuthStatus.INVALID

    def test_invalid_signature_returns_invalid(self):
        s = _make_strategy()
        bad_token = jwt.encode({"sub": "u", "exp": int(time.time()) + 3600}, "other" * 8)
        result = asyncio_run(s.validate_token(bad_token))
        assert result.status == AuthStatus.INVALID

    def test_totally_malformed_token_returns_invalid(self):
        s = _make_strategy()
        result = asyncio_run(s.validate_token("not.a.jwt"))
        assert result.status == AuthStatus.INVALID

    def test_token_with_roles_included_in_result(self):
        s = _make_strategy()
        token = _make_token(roles=["admin", "user"])
        result = asyncio_run(s.validate_token(token))
        assert "admin" in result.user_roles


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------


class TestRefreshToken:
    def _make_refresh_token(self, user_id: str = "user-1") -> str:
        now = int(time.time())
        payload = {
            "sub": user_id,
            "roles": [],
            "permissions": [],
            "type": "refresh",
            "iat": now,
            "exp": now + 86400,
        }
        return jwt.encode(payload, _SECRET, algorithm="HS256")

    def test_valid_refresh_token_returns_new_access_token(self):
        s = _make_strategy()
        refresh = self._make_refresh_token()
        result = asyncio_run(s.refresh_token(refresh))
        assert result.status == AuthStatus.SUCCESS
        assert result.user_id == "user-1"
        assert result.token is not None

    def test_access_token_used_as_refresh_returns_invalid(self):
        s = _make_strategy()
        access_token = _make_token()
        result = asyncio_run(s.refresh_token(access_token))
        assert result.status == AuthStatus.INVALID

    def test_invalid_refresh_token_returns_invalid(self):
        s = _make_strategy()
        result = asyncio_run(s.refresh_token("garbage.token.here"))
        assert result.status == AuthStatus.INVALID


# ---------------------------------------------------------------------------
# revoke_token
# ---------------------------------------------------------------------------


class TestRevokeToken:
    def test_revoke_raises_not_implemented(self):
        s = _make_strategy()
        with pytest.raises(NotImplementedError):
            asyncio_run(s.revoke_token("any_token"))


# ---------------------------------------------------------------------------
# from_auth_config
# ---------------------------------------------------------------------------


class TestFromAuthConfig:
    def _mock_config(self, secret=_SECRET, missing_bearer=False, missing_secret=False):
        from unittest.mock import MagicMock

        cfg = MagicMock()
        if missing_bearer:
            cfg.bearer_token = None
        else:
            bearer = MagicMock()
            if missing_secret:
                bearer.secret_key = None
            else:
                bearer.secret_key = secret
                bearer.secret_key.get_secret_value = MagicMock(return_value=secret)
                bearer.algorithm = "HS256"
                bearer.token_expiry = 3600
            cfg.bearer_token = bearer
        return cfg

    def test_missing_bearer_config_raises_configuration_error(self):
        cfg = self._mock_config(missing_bearer=True)
        with pytest.raises(ConfigurationError, match="bearer_token"):
            BearerTokenStrategy.from_auth_config(cfg)

    def test_missing_secret_key_raises_configuration_error(self):
        cfg = self._mock_config(missing_secret=True)
        with pytest.raises(ConfigurationError):
            BearerTokenStrategy.from_auth_config(cfg)

    def test_short_secret_key_raises_configuration_error(self):
        from unittest.mock import MagicMock

        cfg = MagicMock()
        bearer = MagicMock()
        # Use a mock object that has get_secret_value returning "short"
        secret_mock = MagicMock()
        secret_mock.get_secret_value = MagicMock(return_value="short")
        bearer.secret_key = secret_mock
        bearer.algorithm = "HS256"
        bearer.token_expiry = 3600
        cfg.bearer_token = bearer
        with pytest.raises(ConfigurationError, match="32 bytes"):
            BearerTokenStrategy.from_auth_config(cfg)

    def test_valid_config_returns_strategy(self):
        from unittest.mock import MagicMock

        cfg = MagicMock()
        bearer = MagicMock()
        # Use a mock object that has get_secret_value returning a long secret
        secret_mock = MagicMock()
        secret_mock.get_secret_value = MagicMock(return_value=_SECRET)
        bearer.secret_key = secret_mock
        bearer.algorithm = "HS256"
        bearer.token_expiry = 3600
        cfg.bearer_token = bearer

        strategy = BearerTokenStrategy.from_auth_config(cfg)
        assert isinstance(strategy, BearerTokenStrategy)


# ---------------------------------------------------------------------------
# create_access_token / create_refresh_token
# ---------------------------------------------------------------------------


class TestTokenCreation:
    def test_create_access_token_is_decodable(self):
        s = _make_strategy()
        token = s._create_access_token("u-1", ["admin"], ["read"])
        payload = jwt.decode(token, _SECRET, algorithms=["HS256"])
        assert payload["sub"] == "u-1"
        assert payload["type"] == "access"

    def test_create_refresh_token_is_decodable(self):
        s = _make_strategy()
        token = s.create_refresh_token("u-2", [], [])
        payload = jwt.decode(token, _SECRET, algorithms=["HS256"])
        assert payload["type"] == "refresh"
        assert payload["sub"] == "u-2"

    def test_refresh_token_expiry_is_longer_than_access(self):
        s = _make_strategy(token_expiry=3600)
        access = s._create_access_token("u", [], [])
        refresh = s.create_refresh_token("u", [], [])
        access_exp = jwt.decode(access, _SECRET, algorithms=["HS256"])["exp"]
        refresh_exp = jwt.decode(refresh, _SECRET, algorithms=["HS256"])["exp"]
        assert refresh_exp > access_exp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
