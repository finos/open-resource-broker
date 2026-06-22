"""Unit tests for CognitoAuthStrategy."""

from __future__ import annotations

import time
from base64 import urlsafe_b64encode
from typing import Any
from unittest.mock import MagicMock, patch

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

    # Clear the class-level JWKS cache so each test starts from a clean state.
    CognitoAuthStrategy._jwks_cache.clear()

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
        with patch("orb.providers.aws.auth.cognito_strategy.jwt.decode") as mock_decode:
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

    auth_config = AuthConfig(strategy="cognito")  # type: ignore[call-arg]  # pydantic default fields

    with patch("boto3.client") as _mock:
        _mock.return_value = MagicMock()
        from orb.providers.aws.auth.cognito_strategy import CognitoAuthStrategy

        strategy = CognitoAuthStrategy.from_auth_config(auth_config)

    assert strategy.region == "us-east-1"
    assert strategy.user_pool_id == ""
    assert strategy.client_id == ""


# ---------------------------------------------------------------------------
# _get_public_key — non-RSA key raises InvalidTokenError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_get_public_key_non_rsa_raises():
    """_get_public_key raises InvalidTokenError when the matched JWK is not RSA."""
    import jwt as pyjwt

    strategy = _make_strategy()

    ec_jwks = {
        "keys": [
            {
                "kid": "test-kid",
                "kty": "EC",
                "crv": "P-256",
                "x": "abc",
                "y": "def",
            }
        ]
    }

    mock_response = MagicMock()
    mock_response.json.return_value = ec_jwks

    with patch("requests.get", return_value=mock_response):
        with pytest.raises(pyjwt.InvalidTokenError):
            await strategy._get_public_key("test-kid")


# ---------------------------------------------------------------------------
# validate_token — full round-trip with a real RS256 token
# ---------------------------------------------------------------------------


def _generate_rs256_jwks_and_token(
    user_pool_id: str,
    client_id: str,
    region: str,
    kid: str = "test-key-1",
) -> tuple[dict[str, Any], str]:
    """
    Generate a minimal JWKS dict and a matching RS256 JWT for testing.

    Returns (jwks_dict, encoded_token).
    """
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    # Generate a 2048-bit RSA key pair
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    pub_numbers: RSAPublicNumbers = public_key.public_numbers()  # type: ignore[attr-defined]

    def _int_to_b64url(n: int) -> str:
        byte_length = (n.bit_length() + 7) // 8
        raw = n.to_bytes(byte_length, "big")
        return urlsafe_b64encode(raw).rstrip(b"=").decode()

    jwks: dict[str, Any] = {
        "keys": [
            {
                "kid": kid,
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "n": _int_to_b64url(pub_numbers.n),
                "e": _int_to_b64url(pub_numbers.e),
            }
        ]
    }

    now = int(time.time())
    payload = {
        "sub": "user-cognito-123",
        "cognito:username": "testuser",
        "email": "test@example.com",
        "cognito:groups": ["operators"],
        "token_use": "access",
        "aud": client_id,
        "iss": f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}",
        "iat": now,
        "exp": now + 3600,
    }

    import jwt as pyjwt

    token = pyjwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )
    return jwks, token


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_validate_token_full_rs256_round_trip():
    """
    Full validate_token round-trip: mocked JWKS + real RS256 token must succeed.

    This test would fail if _get_public_key returned a raw JWK dict (the pre-fix
    behaviour) because PyJWT cannot decode with a dict key.
    """
    user_pool_id = "us-east-1_TESTPOOL"
    client_id = "test-client-id"
    region = "us-east-1"

    jwks, token = _generate_rs256_jwks_and_token(user_pool_id, client_id, region)

    mock_response = MagicMock()
    mock_response.json.return_value = jwks

    strategy = _make_strategy()
    strategy.user_pool_id = user_pool_id
    strategy.client_id = client_id
    strategy.region = region

    with patch("requests.get", return_value=mock_response):
        result = await strategy.validate_token(token)

    assert result.status == AuthStatus.SUCCESS
    assert result.user_id == "user-cognito-123"
    assert "operator" in (result.user_roles or [])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_validate_token_unknown_kid_returns_invalid():
    """validate_token returns INVALID when no JWKS entry matches the token's kid."""
    user_pool_id = "us-east-1_TESTPOOL"
    client_id = "test-client-id"
    region = "us-east-1"

    # Generate a valid JWKS+token but serve JWKS with a *different* kid
    jwks, token = _generate_rs256_jwks_and_token(user_pool_id, client_id, region, kid="actual-kid")
    # Replace kid in JWKS so the lookup fails
    jwks["keys"][0]["kid"] = "different-kid"

    mock_response = MagicMock()
    mock_response.json.return_value = jwks

    strategy = _make_strategy()
    strategy.user_pool_id = user_pool_id
    strategy.client_id = client_id
    strategy.region = region

    with patch("requests.get", return_value=mock_response):
        result = await strategy.validate_token(token)

    assert result.status == AuthStatus.INVALID


# ---------------------------------------------------------------------------
# _get_public_key — raise_for_status called on JWKS response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_public_key_calls_raise_for_status():
    """_get_public_key must call raise_for_status() on the JWKS HTTP response.

    A Cognito 403 or 503 returns a non-keys JSON body; without raise_for_status()
    the kid-lookup silently returns None and every token is rejected with an
    opaque "Unable to verify token signature" error that is hard to diagnose.
    """
    strategy = _make_strategy()

    mock_response = MagicMock()
    mock_response.json.return_value = {"keys": []}

    with patch("requests.get", return_value=mock_response) as mock_get:
        await strategy._get_public_key("any-kid")

    mock_response.raise_for_status.assert_called_once()
    mock_get.assert_called_once_with(strategy.jwks_url, timeout=30)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_public_key_non_2xx_propagates_as_invalid_token():
    """An HTTP error from the JWKS endpoint propagates and causes INVALID token result.

    The HTTPError raised by raise_for_status() is caught by the outer except clause
    in _get_public_key, logged, and returns None — which validate_token converts
    to an INVALID AuthResult rather than swallowing the error silently.
    """
    import requests as req

    strategy = _make_strategy()

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = req.exceptions.HTTPError("403 Forbidden")

    with patch(
        "orb.providers.aws.auth.cognito_strategy.jwt.get_unverified_header",
        return_value={"kid": "test-kid"},
    ):
        with patch("requests.get", return_value=mock_response):
            result = await strategy.validate_token("some.bearer.token")

    assert result.status == AuthStatus.INVALID
    assert "Unable to verify token signature" in (result.error_message or "")


# ---------------------------------------------------------------------------
# JWKS caching behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_jwks_fetch_is_cached():
    """_get_public_key called twice with the same kid only fetches JWKS once."""
    strategy = _make_strategy()

    mock_response = MagicMock()
    mock_response.json.return_value = {"keys": []}  # kid not found → returns None both times

    with patch("requests.get", return_value=mock_response) as mock_get:
        await strategy._get_public_key("some-kid")
        await strategy._get_public_key("some-kid")

    # The HTTP fetch should happen only once; second call should hit the cache.
    mock_get.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_jwks_cache_evicted_on_invalid_key_type():
    """Cache entry is evicted when _get_public_key raises InvalidTokenError for a non-RSA key.

    A subsequent call should re-fetch from the endpoint so that a key rotation
    is picked up without requiring a process restart.
    """
    import jwt as pyjwt

    strategy = _make_strategy()

    ec_jwks = {
        "keys": [
            {
                "kid": "ec-kid",
                "kty": "EC",
                "crv": "P-256",
                "x": "abc",
                "y": "def",
            }
        ]
    }

    mock_response = MagicMock()
    mock_response.json.return_value = ec_jwks

    with patch("requests.get", return_value=mock_response) as mock_get:
        # First call: fetches JWKS and raises because key type is not RSA.
        with pytest.raises(pyjwt.InvalidTokenError):
            await strategy._get_public_key("ec-kid")

        # Cache entry must have been evicted on the error.
        assert strategy.jwks_url not in strategy._jwks_cache

        # Second call: must re-fetch (not serve from cache).
        with pytest.raises(pyjwt.InvalidTokenError):
            await strategy._get_public_key("ec-kid")

    assert mock_get.call_count == 2
