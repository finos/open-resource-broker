"""Unit tests for CognitoAuthStrategy."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.infrastructure.adapters.ports.auth import AuthContext, AuthStatus


def _make_context(**kwargs) -> AuthContext:
    defaults: dict[str, Any] = {
        "method": "GET",
        "path": "/api/v1/machines",
        "headers": {},
        "query_params": {},
    }
    defaults.update(kwargs)
    return AuthContext(**defaults)


def _make_logger() -> MagicMock:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_strategy(enabled: bool = True) -> Any:
    from orb.providers.aws.auth.cognito_strategy import CognitoAuthStrategy

    logger = _make_logger()

    with patch("boto3.client") as _mock_boto3:
        _mock_boto3.return_value = MagicMock()
        strategy = CognitoAuthStrategy(
            logger=logger,
            user_pool_id="us-east-1_EXAMPLE",
            client_id="abc123",
            region="us-east-1",
            enabled=enabled,
        )

    return strategy


# ---------------------------------------------------------------------------
# authenticate() — missing Authorization header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_authenticate_missing_header():
    """Cognito authenticate returns FAILED when Authorization header is missing."""
    strategy = _make_strategy()
    result = await strategy.authenticate(_make_context(headers={}))

    assert result.status == AuthStatus.FAILED
    assert "authorization" in (result.error_message or "").lower()


# ---------------------------------------------------------------------------
# authenticate() — disabled strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_authenticate_disabled():
    """Cognito authenticate returns FAILED when strategy is disabled."""
    strategy = _make_strategy(enabled=False)

    result = await strategy.authenticate(
        _make_context(headers={"authorization": "Bearer sometoken"})
    )

    assert result.status == AuthStatus.FAILED
    assert "disabled" in (result.error_message or "").lower()


# ---------------------------------------------------------------------------
# validate_token() — expired token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_validate_token_expired():
    """validate_token returns EXPIRED for an expired JWT."""
    import jwt as pyjwt

    strategy = _make_strategy()

    with patch.object(strategy, "_get_public_key", return_value="fake_key"):
        with patch(
            "orb.providers.aws.auth.cognito_strategy.jwt.decode"
        ) as mock_decode:
            mock_decode.side_effect = pyjwt.ExpiredSignatureError("expired")
            # Need an unverified header
            with patch(
                "orb.providers.aws.auth.cognito_strategy.jwt.get_unverified_header",
                return_value={"kid": "test-kid"},
            ):
                result = await strategy.validate_token("expired.token.here")

    assert result.status == AuthStatus.EXPIRED


# ---------------------------------------------------------------------------
# validate_token() — no key ID in header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_validate_token_missing_kid():
    """validate_token returns INVALID when token header has no kid."""
    import jwt as pyjwt

    strategy = _make_strategy()

    with patch(
        "orb.providers.aws.auth.cognito_strategy.jwt.get_unverified_header",
        return_value={},  # no "kid"
    ):
        result = await strategy.validate_token("some.token.here")

    assert result.status == AuthStatus.INVALID


# ---------------------------------------------------------------------------
# _map_groups_to_roles
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cognito_map_groups_to_roles_admin():
    """Admin group maps to admin role."""
    strategy = _make_strategy()
    roles = strategy._map_groups_to_roles(["admin"])
    assert "admin" in roles


@pytest.mark.unit
def test_cognito_map_groups_to_roles_unknown_group():
    """Unknown groups do not add extra roles beyond the default user role."""
    strategy = _make_strategy()
    roles = strategy._map_groups_to_roles(["unknown-group"])
    assert roles == ["user"]


# ---------------------------------------------------------------------------
# from_auth_config classmethod
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cognito_from_auth_config_defaults():
    """from_auth_config builds strategy with defaults when no sub-config given."""
    from orb.config.schemas.server_schema import AuthConfig

    auth_config = AuthConfig(strategy="cognito")

    with patch("boto3.client") as _mock:
        _mock.return_value = MagicMock()
        from orb.providers.aws.auth.cognito_strategy import CognitoAuthStrategy

        strategy = CognitoAuthStrategy.from_auth_config(auth_config)

    assert strategy.region == "us-east-1"
    assert strategy.user_pool_id == ""
    assert strategy.client_id == ""
