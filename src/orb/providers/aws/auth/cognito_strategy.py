"""AWS Cognito authentication strategy."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from base64 import urlsafe_b64decode
from typing import TYPE_CHECKING, Any, Optional

import boto3
import jwt
import requests
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)
from orb.infrastructure.auth.token_denylist import InMemoryTokenDenylist, TokenDenylistPort
from orb.infrastructure.di.injectable import injectable
from orb.providers.aws.utilities.boto_config import get_boto3_config

_DEFAULT_CONFIG = get_boto3_config()

if TYPE_CHECKING:
    pass

# Minimal structural check for a JWT: three dot-separated base64url segments with a
# coarse length bound to reject obvious garbage before a denylist lookup.
_JWT_STRUCTURAL_RE = re.compile(r"^[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}$")


@injectable
class CognitoAuthStrategy(AuthPort):
    """Authentication strategy using AWS Cognito User Pools."""

    # Class-level JWKS cache: maps jwks_url → {"fetched_at": float, "keys": list[dict]}
    # Shared across all instances so pool restarts benefit from warm entries.
    _jwks_cache: dict[str, dict[str, Any]] = {}
    _cache_ttl_seconds: int = 3600

    def __init__(
        self,
        logger: LoggingPort,
        user_pool_id: str,
        client_id: str,
        region: str = "us-east-1",
        jwks_url: Optional[str] = None,
        enabled: bool = True,
        token_denylist: Optional[TokenDenylistPort] = None,
    ) -> None:
        """
        Initialize Cognito authentication strategy.

        Args:
            user_pool_id: Cognito User Pool ID
            client_id: Cognito App Client ID
            region: AWS region
            jwks_url: JWKS URL for token verification (auto-generated if not provided)
            enabled: Whether this strategy is enabled
            token_denylist: Optional denylist for revoked access tokens. Cognito
                cannot revoke access tokens server-side, so a denylist is required
                to reject them before their natural expiry. If not provided,
                access-token revocation will log a warning and skip denylist
                enforcement; refresh-token revocation via the Cognito API still
                proceeds normally.
        """
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.region = region
        self.enabled = enabled
        self._logger = logger
        self._token_denylist: Optional[TokenDenylistPort] = token_denylist

        # Generate JWKS URL if not provided
        if jwks_url:
            self.jwks_url = jwks_url
        else:
            self.jwks_url = (
                f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
            )

        # Initialize Cognito client
        try:
            self.cognito_client = boto3.client(
                "cognito-idp", region_name=region, config=_DEFAULT_CONFIG
            )
        except Exception as e:
            self._logger.error("Failed to initialize Cognito client: %s", e)
            self.enabled = False

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """
        Authenticate request using Cognito JWT token.

        Args:
            context: Authentication context with Authorization header

        Returns:
            Authentication result based on Cognito token
        """
        if not self.enabled:
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message="Cognito authentication is disabled",
            )

        # Extract Bearer token from Authorization header
        auth_header = context.headers.get("authorization", "")

        if not auth_header.startswith("Bearer "):
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message="Missing or invalid Authorization header",
            )

        token = auth_header[7:]  # Remove "Bearer " prefix
        return await self.validate_token(token)

    async def validate_token(self, token: str) -> AuthResult:
        """
        Validate Cognito JWT token.

        Args:
            token: Cognito JWT token

        Returns:
            Authentication result with user information from Cognito
        """
        try:
            # Structural check: three dot-delimited base64url segments, rough length
            # bound. Rejects obvious garbage before hitting the denylist or JWKS.
            if not _JWT_STRUCTURAL_RE.match(token) or len(token) > 8192:
                return AuthResult(
                    status=AuthStatus.INVALID,
                    error_message="Invalid token format",
                )

            # Check denylist before any cryptographic work (fail fast for revoked tokens).
            if self._token_denylist is not None and await self._token_denylist.is_denylisted(token):
                token_hint = token[-8:]
                self._logger.warning(
                    "Attempted use of revoked Cognito token (suffix=%s)", token_hint
                )
                return AuthResult(
                    status=AuthStatus.INVALID,
                    error_message="Token has been revoked",
                )

            # Decode token without verification first to get header
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")

            if not kid:
                return AuthResult(status=AuthStatus.INVALID, error_message="Token missing key ID")

            # Get public key from JWKS (simplified - in production, cache this)
            public_key = await self._get_public_key(kid)
            if not public_key:
                return AuthResult(
                    status=AuthStatus.INVALID,
                    error_message="Unable to verify token signature",
                )

            # Verify and decode token
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}",
            )

            # Extract user information
            user_id = payload.get("sub")
            username = payload.get("cognito:username", payload.get("username"))
            email = payload.get("email")
            groups = payload.get("cognito:groups", [])

            # Map Cognito groups to roles
            roles = self._map_groups_to_roles(groups)

            # Generate permissions based on roles
            permissions = self._generate_permissions(roles)

            return AuthResult(
                status=AuthStatus.SUCCESS,
                user_id=user_id,
                user_roles=roles,
                permissions=permissions,
                token=token,
                expires_at=payload.get("exp"),
                metadata={
                    "strategy": "cognito",
                    "username": username,
                    "email": email,
                    "cognito_groups": groups,
                    "token_use": payload.get("token_use"),
                    "client_id": payload.get("aud"),
                },
            )

        except jwt.ExpiredSignatureError:
            return AuthResult(status=AuthStatus.EXPIRED, error_message="Token has expired")
        except jwt.InvalidTokenError as e:
            return AuthResult(status=AuthStatus.INVALID, error_message=f"Invalid token: {e!s}")
        except Exception as e:
            self._logger.error("Cognito token validation error: %s", e)
            return AuthResult(status=AuthStatus.FAILED, error_message="Token validation failed")

    async def refresh_token(self, refresh_token: str) -> AuthResult:
        """
        Refresh Cognito access token.

        Args:
            refresh_token: Cognito refresh token

        Returns:
            New authentication result with fresh token
        """
        try:
            response = await asyncio.to_thread(
                self.cognito_client.initiate_auth,
                ClientId=self.client_id,
                AuthFlow="REFRESH_TOKEN_AUTH",
                AuthParameters={"REFRESH_TOKEN": refresh_token},
            )

            auth_result = response.get("AuthenticationResult", {})
            new_access_token = auth_result.get("AccessToken")

            if not new_access_token:
                return AuthResult(status=AuthStatus.FAILED, error_message="Failed to refresh token")

            # Validate the new token to get user info
            return await self.validate_token(new_access_token)

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message=f"Cognito refresh error: {error_code}",
            )
        except Exception as e:
            self._logger.error("Token refresh error: %s", e)
            return AuthResult(status=AuthStatus.FAILED, error_message="Token refresh failed")

    async def revoke_token(self, token: str) -> bool:
        """
        Revoke a Cognito token.

        Cognito supports two token types with different revocation mechanisms:

        - **Refresh tokens**: revoked server-side via the Cognito RevokeToken API,
          AND also inserted into the local denylist.  The denylist insertion ensures
          that even a forged ``token_use`` claim in an unsigned payload cannot allow
          a valid access token to escape denylisting.
        - **Access tokens**: Cognito cannot revoke these server-side.  They are added
          to the local denylist so that subsequent calls to ``validate_token`` reject
          them before their natural expiry.

        Security note: ``token_use`` is read from the UNSIGNED JWT payload.  An
        attacker could craft a valid access token with a forged ``token_use="refresh"``
        to steer into the RevokeToken-only path and thereby skip denylist insertion.
        The fix is to **always** insert the token into the denylist first, then
        additionally call RevokeToken when the unsigned payload indicates a refresh
        token.  The two paths are not mutually exclusive.

        Args:
            token: Cognito JWT token (access or refresh) to revoke

        Returns:
            True if all applicable revocation steps succeeded; False if any failed
            or if no denylist is available (revocation genuinely did not happen).
        """
        try:
            # Extract the unverified JWT payload to determine token type and expiry.
            # The result is untrusted — used only as a hint for the optional Cognito
            # API call; denylist insertion always happens regardless of this value.
            token_use: Optional[str] = None
            expires_at: Optional[int] = None
            try:
                payload_part = token.split(".")[1]
                padding = 4 - len(payload_part) % 4
                if padding != 4:
                    payload_part += "=" * padding
                decoded_payload = json.loads(base64.urlsafe_b64decode(payload_part))
                token_use = decoded_payload.get("token_use")
                expires_at = decoded_payload.get("exp")
            except Exception:
                # Malformed token: proceed with denylist insertion using no expiry.
                pass

            # Step 1: ALWAYS insert into denylist regardless of token_use.
            # This prevents a forged token_use claim from bypassing denylist enforcement.
            if self._token_denylist is None:
                self._logger.warning(
                    "Cognito token cannot be fully revoked: no token denylist is configured. "
                    "The token will remain valid until its natural expiry."
                )
                # Return False — revocation genuinely did not happen.
                return False

            denylist_success = await self._token_denylist.add_token(token, expires_at)
            if denylist_success:
                self._logger.info(
                    "Cognito token added to denylist (token_use=%s, expires_at=%s)",
                    token_use,
                    expires_at,
                )
            else:
                self._logger.error("Failed to add Cognito token to denylist")
                return False

            # Step 2: For refresh tokens, additionally call the Cognito RevokeToken API.
            # This invalidates all access tokens derived from that refresh token on the
            # Cognito side.  The unsigned token_use hint is acceptable here: the worst
            # case of a forged "refresh" claim is a spurious but harmless API call.
            if token_use == "refresh":
                try:
                    await asyncio.to_thread(
                        self.cognito_client.revoke_token,
                        Token=token,
                        ClientId=self.client_id,
                    )
                    self._logger.info("Cognito refresh token revoked via RevokeToken API")
                except ClientError as exc:
                    error_code = exc.response.get("Error", {}).get("Code", "Unknown")
                    self._logger.error(
                        "Cognito RevokeToken API call failed (code=%s); "
                        "token is still in the local denylist",
                        error_code,
                    )
                    # Denylist insertion succeeded so partial revocation did occur;
                    # return False to signal that the Cognito API step failed.
                    return False

            return denylist_success

        except Exception as exc:
            self._logger.error("Token revocation error: %s", exc)
            return False

    @classmethod
    def from_auth_config(cls, auth_config: Any) -> CognitoAuthStrategy:
        """
        Build strategy instance from AuthConfig.

        Extracts the provider_auth.cognito sub-config and constructs a
        CognitoAuthStrategy.  A LoggingPort is obtained from the DI container
        when available; otherwise a plain logging adapter is used.

        Args:
            auth_config: AuthConfig instance with optional provider_auth.cognito sub-config

        Returns:
            Configured CognitoAuthStrategy
        """
        from orb.infrastructure.adapters.logging_adapter import LoggingAdapter

        provider_auth = getattr(auth_config, "provider_auth", None)
        cognito_cfg = getattr(provider_auth, "cognito", None) if provider_auth is not None else None

        user_pool_id: str = (
            getattr(cognito_cfg, "user_pool_id", "") if cognito_cfg is not None else ""
        )
        client_id: str = getattr(cognito_cfg, "client_id", "") if cognito_cfg is not None else ""
        region: str = (
            getattr(cognito_cfg, "region", "us-east-1") if cognito_cfg is not None else "us-east-1"
        )
        jwks_url: Optional[str] = (
            getattr(cognito_cfg, "jwks_url", None) if cognito_cfg is not None else None
        )

        # Intentional service-locator: from_auth_config is called by the
        # AuthRegistry as ``strategy_factory.from_auth_config(auth_config)``
        # with a fixed signature — no logger or denylist parameters can be
        # threaded through without changing the AuthRegistry protocol (a broad,
        # cross-cutting change).  The DI container is therefore queried here as
        # a best-effort bootstrap; plain fallbacks ensure this classmethod
        # remains self-contained when the container is not yet initialised.
        try:
            from orb.domain.base.ports import LoggingPort
            from orb.infrastructure.di.container import get_container

            _container = get_container()
            logger: LoggingPort = _container.get(LoggingPort)
        except Exception:
            logger = LoggingAdapter()  # type: ignore[assignment]
            _container = None

        # Resolve a TokenDenylistPort from the container so that access-token
        # revocation is enforced in production.  Fall back to an in-process
        # InMemoryTokenDenylist when the container has none registered — this
        # guarantees _token_denylist is always non-None on live instances.
        token_denylist: TokenDenylistPort
        try:
            if _container is not None:
                token_denylist = _container.get(TokenDenylistPort)
            else:
                token_denylist = InMemoryTokenDenylist()
        except Exception:
            token_denylist = InMemoryTokenDenylist()

        return cls(
            logger=logger,
            user_pool_id=user_pool_id,
            client_id=client_id,
            region=region,
            jwks_url=jwks_url,
            enabled=True,
            token_denylist=token_denylist,
        )

    def get_strategy_name(self) -> str:
        """
        Get strategy name.

        Returns:
            Strategy name
        """
        return "cognito"

    def is_enabled(self) -> bool:
        """
        Check if strategy is enabled.

        Returns:
            Whether strategy is enabled
        """
        return self.enabled

    @staticmethod
    def _b64url_to_int(val: str) -> int:
        """Decode a base64url-encoded string to an integer (used for RSA key fields)."""
        padded = val + "=" * (-len(val) % 4)
        return int.from_bytes(urlsafe_b64decode(padded), "big")

    async def _get_public_key(self, kid: str) -> Any:
        """
        Get public key from Cognito JWKS endpoint.

        Fetches the JWKS (using the class-level cache; re-fetches after TTL),
        locates the entry matching ``kid``, and converts it to a ``cryptography``
        RSAPublicKey object suitable for use with PyJWT.

        The synchronous ``requests.get`` call is offloaded via
        ``asyncio.to_thread`` so the event loop is not blocked during the fetch.

        On ``jwt.InvalidTokenError`` (non-RSA key) the cache entry is evicted so
        that a key rotation does not lock out valid tokens indefinitely.

        Args:
            kid: Key ID from token header

        Returns:
            RSAPublicKey for token verification, or None if not found

        Raises:
            jwt.InvalidTokenError: If the matched key is not an RSA key
        """
        try:
            jwks = await self._fetch_jwks_cached()

            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    if key.get("kty") != "RSA":
                        # Evict the cache entry so a subsequent rotation is picked up.
                        self._jwks_cache.pop(self.jwks_url, None)
                        raise jwt.InvalidTokenError(
                            f"Unsupported key type: {key.get('kty')!r}. Only RSA keys are supported."
                        )
                    n = self._b64url_to_int(key["n"])
                    e = self._b64url_to_int(key["e"])
                    return RSAPublicNumbers(e=e, n=n).public_key()

            return None

        except jwt.InvalidTokenError:
            raise
        except Exception as e:
            self._logger.error("Failed to get public key: %s", e)
            return None

    async def _fetch_jwks_cached(self) -> dict[str, Any]:
        """Return the JWKS document for this strategy's endpoint.

        Serves from the class-level cache when the entry is younger than
        ``_cache_ttl_seconds``; otherwise fetches fresh via
        ``asyncio.to_thread`` and repopulates the cache.
        """
        cached = self._jwks_cache.get(self.jwks_url)
        if cached is not None:
            age = time.monotonic() - cached["fetched_at"]
            if age < self._cache_ttl_seconds:
                return cached["jwks"]  # type: ignore[return-value]

        def _do_fetch() -> dict[str, Any]:
            response = requests.get(self.jwks_url, timeout=30)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]

        jwks: dict[str, Any] = await asyncio.to_thread(_do_fetch)
        self._jwks_cache[self.jwks_url] = {"fetched_at": time.monotonic(), "jwks": jwks}
        return jwks

    def _map_groups_to_roles(self, groups: list[str]) -> list[str]:
        """
        Map Cognito groups to application roles.

        Args:
            groups: Cognito user groups

        Returns:
            Application roles
        """
        roles = ["user"]  # Default role

        group_role_mapping = {
            "admin": "admin",
            "administrators": "admin",
            "operators": "operator",
            "viewers": "viewer",
            "service-accounts": "service_account",
        }

        for group in groups:
            role = group_role_mapping.get(group.lower())
            if role and role not in roles:
                roles.append(role)

        return roles

    def _generate_permissions(self, roles: list[str]) -> list[str]:
        """
        Generate permissions based on roles.

        Args:
            roles: User roles

        Returns:
            List of permissions
        """
        permissions = []

        # Base permissions for all users
        permissions.extend(["hostfactory:list_templates", "hostfactory:get_status"])

        # Role-based permissions
        if "admin" in roles:
            permissions.extend(["hostfactory:*", "system:*"])
        elif "operator" in roles:
            permissions.extend(
                [
                    "hostfactory:request_machines",
                    "hostfactory:return_machines",
                    "hostfactory:manage_requests",
                ]
            )
        elif "viewer" in roles:
            permissions.extend(["hostfactory:view_*"])

        return list(set(permissions))  # Remove duplicates
