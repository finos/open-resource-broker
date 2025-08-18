"""Bearer token authentication strategy."""

import time
from typing import List

import jwt

from infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)
from infrastructure.logging.logger import get_logger


class BearerTokenStrategy(AuthPort):
    """Authentication strategy using Bearer tokens (JWT)."""

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        token_expiry: int = 3600,  # 1 hour
        enabled: bool = True,
    ):
        """
        Initialize bearer token strategy.

        Args:
            secret_key: Secret key for JWT signing/verification
            algorithm: JWT algorithm to use
            token_expiry: Token expiry time in seconds
            enabled: Whether this strategy is enabled
        """
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.token_expiry = token_expiry
        self.enabled = enabled
        self.logger = get_logger(__name__)

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """
        Authenticate request using Bearer token.

        Args:
            context: Authentication context with request headers

        Returns:
            Authentication result
        """
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
        Validate JWT token.

        Args:
            token: JWT token to validate

        Returns:
            Authentication result with user information
        """
        try:
            # Decode and verify JWT token
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])

            # Check token expiration
            exp = payload.get("exp")
            if exp and time.time() > exp:
                return AuthResult(status=AuthStatus.EXPIRED, error_message="Token has expired")

            # Extract user information from token
            user_id = payload.get("sub")
            user_roles = payload.get("roles", [])
            permissions = payload.get("permissions", [])

            if not user_id:
                return AuthResult(status=AuthStatus.INVALID, error_message="Token missing user ID")

            self.logger.debug("Token validated for user: %s", user_id)

            return AuthResult(
                status=AuthStatus.SUCCESS,
                user_id=user_id,
                user_roles=user_roles,
                permissions=permissions,
                token=token,
                expires_at=exp,
                metadata={
                    "strategy": "bearer_token",
                    "algorithm": self.algorithm,
                    "issued_at": payload.get("iat"),
                    "issuer": payload.get("iss"),
                },
            )

        except jwt.ExpiredSignatureError:
            return AuthResult(status=AuthStatus.EXPIRED, error_message="Token has expired")
        except jwt.InvalidTokenError as e:
            return AuthResult(status=AuthStatus.INVALID, error_message=f"Invalid token: {str(e)}")
        except Exception as e:
            self.logger.error("Token validation error: %s", e)
            return AuthResult(status=AuthStatus.FAILED, error_message="Token validation failed")

    async def refresh_token(self, refresh_token: str) -> AuthResult:
        """
        Refresh access token using refresh token.

        Args:
            refresh_token: Refresh token

        Returns:
            New authentication result with fresh token
        """
        try:
            # Validate refresh token
            payload = jwt.decode(refresh_token, self.secret_key, algorithms=[self.algorithm])

            # Check if it's actually a refresh token
            token_type = payload.get("type")
            if (
                token_type != "refresh"  # nosec B105
            ):  # This is a token type identifier, not a password
                return AuthResult(status=AuthStatus.INVALID, error_message="Invalid refresh token")

            # Create new access token
            user_id = payload.get("sub")
            user_roles = payload.get("roles", [])
            permissions = payload.get("permissions", [])

            new_token = self._create_access_token(user_id, user_roles, permissions)

            return AuthResult(
                status=AuthStatus.SUCCESS,
                user_id=user_id,
                user_roles=user_roles,
                permissions=permissions,
                token=new_token,
                expires_at=int(time.time()) + self.token_expiry,
                metadata={"strategy": "bearer_token", "refreshed": True},
            )

        except jwt.InvalidTokenError as e:
            return AuthResult(
                status=AuthStatus.INVALID,
                error_message=f"Invalid refresh token: {str(e)}",
            )
        except Exception as e:
            self.logger.error("Token refresh error: %s", e)
            return AuthResult(status=AuthStatus.FAILED, error_message="Token refresh failed")

    async def revoke_token(self, token: str) -> bool:
        """
        Revoke token (add to blacklist).

        Note: This is a simplified implementation. In production,
        you would maintain a token blacklist in a database or cache.

        Args:
            token: Token to revoke

        Returns:
            True if token was revoked
        """
        self.logger.info("Token revocation requested (not implemented)")
        return True

    def get_strategy_name(self) -> str:
        """
        Get strategy name.

        Returns:
            Strategy name
        """
        return "bearer_token"

    def is_enabled(self) -> bool:
        """
        Check if strategy is enabled.

        Returns:
            Whether strategy is enabled
        """
        return self.enabled

    def _create_access_token(self, user_id: str, roles: List[str], permissions: List[str]) -> str:
        """
        Create a new access token.

        Args:
            user_id: User identifier
            roles: User roles
            permissions: User permissions

        Returns:
            JWT access token
        """
        now = int(time.time())
        payload = {
            "sub": user_id,
            "roles": roles,
            "permissions": permissions,
            "type": "access",
            "iat": now,
            "exp": now + self.token_expiry,
            "iss": "open-hostfactory-plugin",
        }

        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(self, user_id: str, roles: List[str], permissions: List[str]) -> str:
        """
        Create a refresh token.

        Args:
            user_id: User identifier
            roles: User roles
            permissions: User permissions

        Returns:
            JWT refresh token
        """
        now = int(time.time())
        payload = {
            "sub": user_id,
            "roles": roles,
            "permissions": permissions,
            "type": "refresh",
            "iat": now,
            "exp": now + (self.token_expiry * 24),  # Refresh tokens last 24x longer
            "iss": "open-hostfactory-plugin",
        }

        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
