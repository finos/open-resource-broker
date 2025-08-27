"""No authentication strategy - allows all requests."""

from infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)
from infrastructure.logging.logger import get_logger


class NoAuthStrategy(AuthPort):
    """Authentication strategy that allows all requests without authentication."""

    def __init__(self, enabled: bool = False) -> None:
        """
        Initialize no-auth strategy.

        Args:
            enabled: Whether this strategy is enabled (typically False for no-auth)
        """
        self.enabled = enabled
        self.logger = get_logger(__name__)

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """
        Allow all requests without authentication.

        Args:
            context: Authentication context (ignored)

        Returns:
            Successful authentication result with anonymous user
        """
        self.logger.debug("No-auth strategy: allowing request to %s", context.path)

        return AuthResult(
            status=AuthStatus.SUCCESS,
            user_id="anonymous",
            user_roles=["anonymous"],
            permissions=["*"],  # Grant all permissions for no-auth
            metadata={"strategy": "no_auth", "authenticated": False},
        )

    async def validate_token(self, token: str) -> AuthResult:
        """
        Token validation not applicable for no-auth strategy.

        Args:
            token: Token to validate (ignored)

        Returns:
            Successful result for any token
        """
        return AuthResult(
            status=AuthStatus.SUCCESS,
            user_id="anonymous",
            user_roles=["anonymous"],
            permissions=["*"],
            metadata={"strategy": "no_auth", "token_validation": "skipped"},
        )

    async def refresh_token(self, refresh_token: str) -> AuthResult:
        """
        Token refresh not applicable for no-auth strategy.

        Args:
            refresh_token: Refresh token (ignored)

        Returns:
            Successful result without actual token
        """
        return AuthResult(
            status=AuthStatus.SUCCESS,
            user_id="anonymous",
            metadata={"strategy": "no_auth", "token_refresh": "not_applicable"},
        )

    async def revoke_token(self, token: str) -> bool:
        """
        Token revocation not applicable for no-auth strategy.

        Args:
            token: Token to revoke (ignored)

        Returns:
            Always True (no-op)
        """
        return True

    def get_strategy_name(self) -> str:
        """
        Get strategy name.

        Returns:
            Strategy name
        """
        return "none"

    def is_enabled(self) -> bool:
        """
        Check if strategy is enabled.

        Returns:
            Whether strategy is enabled
        """
        return self.enabled
