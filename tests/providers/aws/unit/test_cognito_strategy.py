"""Unit tests for CognitoAuthStrategy."""

from __future__ import annotations

import time
from base64 import urlsafe_b64encode
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


def _make_strategy(enabled: bool = True, token_denylist=None) -> Any:
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
            token_denylist=token_denylist,
        )

    return strategy


def _make_mock_denylist(is_denylisted: bool = False, add_token_returns: bool = True):
    """Build an AsyncMock that satisfies the TokenDenylistPort interface."""
    denylist = MagicMock()
    denylist.is_denylisted = AsyncMock(return_value=is_denylisted)
    denylist.add_token = AsyncMock(return_value=add_token_returns)
    return denylist


def _make_fake_jwt_token(token_use: str = "access", exp_offset: int = 3600) -> str:
    """Build a syntactically valid (three-part) fake JWT with the given payload fields.

    The token is NOT cryptographically signed — it is used only to test code paths
    that read the unsigned payload (revoke_token, structural check).
    """
    import base64
    import json

    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    now = int(time.time())
    payload_data = {
        "sub": "user-123",
        "token_use": token_use,
        "exp": now + exp_offset,
        "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE",
        "aud": "abc123",
    }
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
    sig = "fakesignatureAABBCC"
    return f"{header}.{payload}.{sig}"


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
    token = _make_fake_jwt_token()

    with patch.object(strategy, "_get_public_key", return_value="fake_key"):
        with patch("orb.providers.aws.auth.cognito_strategy.jwt.decode") as mock_decode:
            mock_decode.side_effect = pyjwt.ExpiredSignatureError("expired")
            # Need an unverified header
            with patch(
                "orb.providers.aws.auth.cognito_strategy.jwt.get_unverified_header",
                return_value={"kid": "test-kid"},
            ):
                result = await strategy.validate_token(token)

    assert result.status == AuthStatus.EXPIRED


# ---------------------------------------------------------------------------
# validate_token() — no key ID in header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_validate_token_missing_kid():
    """validate_token returns INVALID when token header has no kid."""

    strategy = _make_strategy()
    token = _make_fake_jwt_token()

    with patch(
        "orb.providers.aws.auth.cognito_strategy.jwt.get_unverified_header",
        return_value={},  # no "kid"
    ):
        result = await strategy.validate_token(token)

    assert result.status == AuthStatus.INVALID


# ---------------------------------------------------------------------------
# validate_token() — structural check rejects garbage tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_validate_token_rejects_garbage():
    """validate_token returns INVALID for strings that are not JWT-shaped."""
    strategy = _make_strategy()

    for bad_token in ("", "notaJWT", "two.parts", "x" * 9000):
        result = await strategy.validate_token(bad_token)
        assert result.status == AuthStatus.INVALID, f"expected INVALID for {bad_token!r}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_validate_token_structural_check_accepts_real_shape():
    """validate_token does NOT reject a properly shaped token at the structural gate."""
    strategy = _make_strategy()
    token = _make_fake_jwt_token()

    # The structural regex should pass; subsequent failure is expected (bad sig / key),
    # but the error must NOT be "Invalid token format".
    with patch(
        "orb.providers.aws.auth.cognito_strategy.jwt.get_unverified_header",
        return_value={},  # triggers "Token missing key ID", not structural failure
    ):
        result = await strategy.validate_token(token)

    assert result.error_message != "Invalid token format"


# ---------------------------------------------------------------------------
# validate_token() — denylist check rejects revoked tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_validate_token_rejects_denylisted_token():
    """validate_token returns INVALID when the token is on the denylist."""
    denylist = _make_mock_denylist(is_denylisted=True)
    strategy = _make_strategy(token_denylist=denylist)
    token = _make_fake_jwt_token()

    result = await strategy.validate_token(token)

    assert result.status == AuthStatus.INVALID
    assert "revoked" in (result.error_message or "").lower()
    denylist.is_denylisted.assert_called_once_with(token)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_validate_token_revoked_warning_does_not_leak_token():
    """The warning log for a revoked token must not contain the full token value."""
    denylist = _make_mock_denylist(is_denylisted=True)
    strategy = _make_strategy(token_denylist=denylist)
    token = _make_fake_jwt_token()

    await strategy.validate_token(token)

    # Collect all warning calls and confirm the raw token is absent.
    for call_args in strategy._logger.warning.call_args_list:
        args, kwargs = call_args
        rendered = " ".join(str(a) for a in args)
        assert token not in rendered, "Full token leaked in warning log"


# ---------------------------------------------------------------------------
# validate_token() — error_message must not contain the raw token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_validate_token_error_message_no_raw_token():
    """validate_token error messages must never echo back the raw token."""
    import jwt as pyjwt

    strategy = _make_strategy()
    token = _make_fake_jwt_token()

    # Force InvalidTokenError to exercise the except branch
    with patch(
        "orb.providers.aws.auth.cognito_strategy.jwt.get_unverified_header",
        side_effect=pyjwt.InvalidTokenError("bad"),
    ):
        result = await strategy.validate_token(token)

    assert token not in (result.error_message or ""), "Raw token leaked in error_message"


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
        # get_container is a local import inside from_auth_config; patch at source.
        with patch(
            "orb.infrastructure.di.container.get_container",
            side_effect=Exception("no container"),
        ):
            from orb.providers.aws.auth.cognito_strategy import CognitoAuthStrategy

            strategy = CognitoAuthStrategy.from_auth_config(auth_config)

    assert strategy.region == "us-east-1"
    assert strategy.user_pool_id == ""
    assert strategy.client_id == ""


@pytest.mark.unit
def test_cognito_from_auth_config_produces_non_none_denylist():
    """from_auth_config must always produce an instance with a non-None denylist.

    This test would FAIL on the pre-fix code where from_auth_config never
    injected token_denylist (leaving it as None on every live instance).
    """
    from orb.config.schemas.server_schema import AuthConfig
    from orb.infrastructure.auth.token_denylist import TokenDenylistPort

    auth_config = AuthConfig(strategy="cognito")  # type: ignore[call-arg]

    with patch("boto3.client") as _mock:
        _mock.return_value = MagicMock()
        # Let the container resolution fail so the InMemoryTokenDenylist fallback fires.
        with patch(
            "orb.infrastructure.di.container.get_container",
            side_effect=Exception("no container"),
        ):
            from orb.providers.aws.auth.cognito_strategy import CognitoAuthStrategy

            strategy = CognitoAuthStrategy.from_auth_config(auth_config)

    assert strategy._token_denylist is not None
    assert isinstance(strategy._token_denylist, TokenDenylistPort)


@pytest.mark.unit
def test_cognito_from_auth_config_uses_container_denylist_when_available():
    """from_auth_config resolves TokenDenylistPort from the DI container when present."""
    from orb.config.schemas.server_schema import AuthConfig
    from orb.infrastructure.auth.token_denylist import TokenDenylistPort

    auth_config = AuthConfig(strategy="cognito")  # type: ignore[call-arg]

    mock_denylist = _make_mock_denylist()
    mock_container = MagicMock()
    mock_container.get = MagicMock(
        side_effect=lambda cls: mock_denylist if cls is TokenDenylistPort else MagicMock()
    )

    with patch("boto3.client") as _mock:
        _mock.return_value = MagicMock()
        # get_container is a local import; patch at source.
        with patch(
            "orb.infrastructure.di.container.get_container",
            return_value=mock_container,
        ):
            from orb.providers.aws.auth.cognito_strategy import CognitoAuthStrategy

            strategy = CognitoAuthStrategy.from_auth_config(auth_config)

    assert strategy._token_denylist is mock_denylist


# ---------------------------------------------------------------------------
# revoke_token() — zero-test gap filled (H4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_revoke_refresh_token_calls_cognito_api():
    """revoke_token calls cognito_client.revoke_token for a refresh token.

    This test FAILS on the pre-fix code where a refresh token would skip
    denylist insertion and only call RevokeToken — the mock denylist would
    show zero calls.
    """
    denylist = _make_mock_denylist()
    strategy = _make_strategy(token_denylist=denylist)
    strategy.cognito_client = MagicMock()
    strategy.cognito_client.revoke_token = MagicMock(return_value={})

    token = _make_fake_jwt_token(token_use="refresh")

    with patch(
        "orb.providers.aws.auth.cognito_strategy.asyncio.to_thread",
        new=AsyncMock(side_effect=lambda fn, **kw: fn(**kw)),
    ):
        result = await strategy.revoke_token(token)

    assert result is True
    strategy.cognito_client.revoke_token.assert_called_once_with(
        Token=token,
        ClientId=strategy.client_id,
    )
    # Denylist insertion must also have happened (H6 fix).
    denylist.add_token.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_revoke_access_token_adds_to_denylist():
    """revoke_token adds an access token to the denylist and does NOT call Cognito API.

    This test FAILS on the pre-fix code if _token_denylist is None (the live
    behaviour before this fix), since add_token would never be called.
    """
    denylist = _make_mock_denylist()
    strategy = _make_strategy(token_denylist=denylist)
    strategy.cognito_client = MagicMock()

    token = _make_fake_jwt_token(token_use="access")
    result = await strategy.revoke_token(token)

    assert result is True
    denylist.add_token.assert_called_once()
    # RevokeToken must NOT be called for access tokens.
    strategy.cognito_client.revoke_token.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_revoke_token_returns_false_without_denylist():
    """revoke_token returns False when no denylist is configured.

    Before the fix this returned True even though nothing was revoked.
    This test FAILS on the pre-fix code.
    """
    strategy = _make_strategy(token_denylist=None)
    token = _make_fake_jwt_token(token_use="access")

    result = await strategy.revoke_token(token)

    assert result is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_revoke_refresh_token_without_denylist_returns_false():
    """revoke_token returns False for a refresh token when no denylist is available.

    On the pre-fix code a refresh token would skip denylist and call RevokeToken,
    returning True — even though the local denylist was never populated and the
    token could be re-used against any route that doesn't call Cognito.
    """
    strategy = _make_strategy(token_denylist=None)
    token = _make_fake_jwt_token(token_use="refresh")

    result = await strategy.revoke_token(token)

    assert result is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_revoke_token_forged_token_use_still_denylists():
    """A token with forged token_use='refresh' is still added to the denylist (H6).

    An attacker could craft a valid access JWT with token_use='refresh' in the
    UNSIGNED payload to steer the pre-fix code into the Cognito RevokeToken path
    (which would fail with an error for an access token) and thereby avoid being
    denylisted.

    After the fix, denylist insertion happens FIRST for ALL tokens regardless of
    the unsigned token_use claim.  This test verifies that even if token_use
    claims 'refresh', the denylist is still populated.
    """
    denylist = _make_mock_denylist()
    strategy = _make_strategy(token_denylist=denylist)
    # Simulate a Cognito client that raises on revoke_token (access token presented
    # as refresh — Cognito rejects it).
    mock_client = MagicMock()
    from botocore.exceptions import ClientError

    mock_client.revoke_token.side_effect = ClientError(
        {"Error": {"Code": "InvalidParameterException", "Message": "Not a refresh token"}},
        "RevokeToken",
    )
    strategy.cognito_client = mock_client

    # Build a token whose unsigned payload claims token_use="refresh"
    token = _make_fake_jwt_token(token_use="refresh")

    with patch(
        "orb.providers.aws.auth.cognito_strategy.asyncio.to_thread",
        new=AsyncMock(side_effect=lambda fn, **kw: fn(**kw)),
    ):
        result = await strategy.revoke_token(token)

    # The Cognito API step failed, so result is False — but denylist MUST have been
    # populated before the API call was attempted.
    denylist.add_token.assert_called_once()
    assert result is False  # API step failed → False, but denylist is populated


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_revoke_token_denylist_failure_returns_false():
    """revoke_token returns False when the denylist add_token call fails."""
    denylist = _make_mock_denylist(add_token_returns=False)
    strategy = _make_strategy(token_denylist=denylist)
    token = _make_fake_jwt_token(token_use="access")

    result = await strategy.revoke_token(token)

    assert result is False


# ---------------------------------------------------------------------------
# validate_token() — denylisted token is rejected after revoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognito_validate_token_rejects_after_revoke():
    """validate_token rejects a token that was previously revoked (end-to-end denylist check).

    This test uses a real InMemoryTokenDenylist to confirm the full round-trip:
    revoke_token adds to denylist → validate_token reads denylist → INVALID.
    """
    from orb.infrastructure.auth.token_denylist import InMemoryTokenDenylist

    denylist = InMemoryTokenDenylist()
    strategy = _make_strategy(token_denylist=denylist)
    strategy.cognito_client = MagicMock()

    token = _make_fake_jwt_token(token_use="access")

    # Step 1: revoke
    revoked = await strategy.revoke_token(token)
    assert revoked is True

    # Step 2: validate should now reject
    result = await strategy.validate_token(token)
    assert result.status == AuthStatus.INVALID
    assert "revoked" in (result.error_message or "").lower()


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
