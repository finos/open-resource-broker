"""Enhanced bearer token authentication strategy with blacklist and rate limiting."""

import base64
import json
import time
from collections import defaultdict

import jwt

from orb.infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)
from orb.infrastructure.auth.token_blacklist import TokenBlacklistPort
from orb.infrastructure.logging.logger import get_logger


class RateLimiter:
    """Simple rate limiter for token validation."""

    def __init__(self, max_attempts: int = 10, window_seconds: int = 60) -> None:
        """
        Initialize rate limiter.

        Args:
            max_attempts: Maximum attempts per window
            window_seconds: Time window in seconds
        """
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._logger = get_logger(__name__)

    def is_rate_limited(self, identifier: str) -> bool:
        """
        Check if identifier is rate limited.

        Args:
            identifier: IP address or user identifier

        Returns:
            True if rate limited
        """
        current_time = time.time()
        cutoff_time = current_time - self._window_seconds

        # Clean old attempts
        self._attempts[identifier] = [t for t in self._attempts[identifier] if t > cutoff_time]

        # Check if rate limited
        if len(self._attempts[identifier]) >= self._max_attempts:
            self._logger.warning("Rate limit exceeded for %s", identifier)
            return True

        # Record attempt
        self._attempts[identifier].append(current_time)
        return False


class EnhancedBearerTokenStrategy(AuthPort):
    """Enhanced authentication strategy with JWT blacklist and rate limiting."""

    def __init__(
        self,
        secret_key: str,
        blacklist: TokenBlacklistPort,
        algorithm: str = "HS256",
        token_expiry: int = 3600,
        enabled: bool = True,
        rate_limit_enabled: bool = True,
        max_attempts: int = 10,
        rate_window: int = 60,
    ) -> None:
        """
        Initialize enhanced bearer token strategy.

        Args:
            secret_key: Secret key for JWT signing/verification
            blacklist: Token blacklist implementation
            algorithm: JWT algorithm to use
            token_expiry: Token expiry time in seconds
            enabled: Whether this strategy is enabled
            rate_limit_enabled: Whether rate limiting is enabled
            max_attempts: Maximum validation attempts per window
            rate_window: Rate limit window in seconds
        """
        self.secret_key = secret_key
        self.blacklist = blacklist
        self.algorithm = algorithm
        self.token_expiry = token_expiry
        self.enabled = enabled
        self.rate_limit_enabled = rate_limit_enabled
        self.rate_limiter = RateLimiter(max_attempts, rate_window)
        self.logger = get_logger(__name__)

        # Validate secret key strength (minimum 256 bits = 32 bytes)
        if len(secret_key.encode()) < 32:
            self.logger.warning(
                "Secret key is weak (< 256 bits). Use a stronger key for production."
            )

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """
        Authenticate request using Bearer token.

        Args:
            context: Authentication context with request headers

        Returns:
            Authentication result
        """
        # Rate limiting by IP
        if self.rate_limit_enabled and context.client_ip:
            if self.rate_limiter.is_rate_limited(context.client_ip):
                self.logger.warning("Rate limit exceeded for IP: %s", context.client_ip)
                return AuthResult(
                    status=AuthStatus.FAILED,
                    error_message="Too many authentication attempts",
                )

        # Extract Bearer token from Authorization header
        auth_header = context.headers.get("authorization", "")

        if not auth_header.startswith("Bearer "):
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message="Missing or invalid Authorization header",
            )

        token = auth_header[7:].strip()

        # Reject empty tokens
        if not token:
            return AuthResult(
                status=AuthStatus.INVALID,
                error_message="Empty token",
            )

        # Reject tokens with invalid characters (header injection attempts)
        if not all(c.isalnum() or c in "._-" for c in token):
            return AuthResult(
                status=AuthStatus.INVALID,
                error_message="Invalid token format",
            )

        return await self.validate_token(token)

    async def validate_token(self, token: str) -> AuthResult:
        """
        Validate JWT token with blacklist check.

        Args:
            token: JWT token to validate

        Returns:
            Authentication result with user information
        """
        try:
            # Check blacklist first (fail fast)
            if await self.blacklist.is_blacklisted(token):
                self.logger.warning("Attempted use of revoked JWT")
                return AuthResult(
                    status=AuthStatus.INVALID,
                    error_message="Token has been revoked",
                )

            # Decode and verify JWT token
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "require": ["exp", "iat", "sub"],
                },
            )

            # Extract user information from token
            user_id = payload.get("sub")
            user_roles = payload.get("roles", [])
            permissions = payload.get("permissions", [])
            exp = payload.get("exp")

            if not user_id:
                return AuthResult(status=AuthStatus.INVALID, error_message="Token missing user ID")

            self.logger.debug("Auth validated for user: %s", user_id)

            return AuthResult(
                status=AuthStatus.SUCCESS,
                user_id=user_id,
                user_roles=user_roles,
                permissions=permissions,
                token=token,
                expires_at=exp,
                metadata={
                    "strategy": "enhanced_bearer_token",
                    "algorithm": self.algorithm,
                    "issued_at": payload.get("iat"),
                    "issuer": payload.get("iss"),
                },
            )

        except jwt.ExpiredSignatureError:
            return AuthResult(status=AuthStatus.EXPIRED, error_message="Token has expired")
        except jwt.InvalidTokenError as e:
            self.logger.warning("JWT validation failed: %s", str(e))
            return AuthResult(status=AuthStatus.INVALID, error_message="Invalid token")
        except Exception as e:
            self.logger.error("Auth validation error: %s", e)
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
            # Check blacklist
            if await self.blacklist.is_blacklisted(refresh_token):
                return AuthResult(
                    status=AuthStatus.INVALID,
                    error_message="Refresh token has been revoked",
                )

            # Validate refresh token
            payload = jwt.decode(
                refresh_token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={"verify_signature": True, "verify_exp": True},
            )

            # Check if it's actually a refresh token
            token_type = payload.get("type")
            if token_type != "refresh":
                return AuthResult(status=AuthStatus.INVALID, error_message="Invalid refresh token")

            # Create new access token
            user_id = payload.get("sub")
            user_roles = payload.get("roles", [])
            permissions = payload.get("permissions", [])

            new_token = self._create_access_token(user_id or "", user_roles, permissions)

            return AuthResult(
                status=AuthStatus.SUCCESS,
                user_id=user_id,
                user_roles=user_roles,
                permissions=permissions,
                token=new_token,
                expires_at=int(time.time()) + self.token_expiry,
                metadata={"strategy": "enhanced_bearer_token", "refreshed": True},
            )

        except jwt.InvalidTokenError as e:
            return AuthResult(
                status=AuthStatus.INVALID,
                error_message=f"Invalid refresh token: {e!s}",
            )
        except Exception as e:
            self.logger.error("Auth refresh error: %s", e)
            return AuthResult(status=AuthStatus.FAILED, error_message="Token refresh failed")

    async def revoke_token(self, token: str) -> bool:
        """
        Revoke token by adding to blacklist.

        Args:
            token: Token to revoke

        Returns:
            True if token was revoked
        """
        try:
            # Extract expiration from JWT payload without verification
            # (token is being revoked, we only need exp for blacklist TTL)
            try:
                payload_part = token.split(".")[1]
                # Add padding if needed
                padding = 4 - len(payload_part) % 4
                if padding != 4:
                    payload_part += "=" * padding
                decoded = json.loads(base64.urlsafe_b64decode(payload_part))
                expires_at = decoded.get("exp")
            except Exception:
                expires_at = None

            # Add to blacklist
            success = await self.blacklist.add_token(token, expires_at)

            if success:
                self.logger.info("JWT revoked and added to blacklist")
            else:
                self.logger.error("Failed to add JWT to blacklist")

            return success

        except Exception as e:
            self.logger.error("Auth revocation error: %s", e)
            return False

    def get_strategy_name(self) -> str:
        """Get strategy name."""
        return "enhanced_bearer_token"

    def is_enabled(self) -> bool:
        """Check if strategy is enabled."""
        return self.enabled

    def _create_access_token(self, user_id: str, roles: list[str], permissions: list[str]) -> str:
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
            "iss": "open-resource-broker",
        }

        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(self, user_id: str, roles: list[str], permissions: list[str]) -> str:
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
            "exp": now + (self.token_expiry * 24),
            "iss": "open-resource-broker",
        }

        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
