"""Token management port interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional


class TokenType(Enum):
    """Token type enumeration."""

    ACCESS = "access"
    REFRESH = "refresh"
    ID = "id"
    API_KEY = "api_key"


@dataclass
class TokenResult:
    """Result of token operation."""

    success: bool
    token: Optional[str] = None
    token_type: Optional[TokenType] = None
    expires_in: Optional[int] = None  # Seconds until expiration
    expires_at: Optional[int] = None  # Unix timestamp
    refresh_token: Optional[str] = None
    scope: List[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.scope is None:
            self.scope = []
        if self.metadata is None:
            self.metadata = {}


@dataclass
class TokenValidationResult:
    """Result of token validation."""

    valid: bool
    user_id: Optional[str] = None
    client_id: Optional[str] = None
    scope: List[str] = None
    expires_at: Optional[int] = None
    issued_at: Optional[int] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.scope is None:
            self.scope = []
        if self.metadata is None:
            self.metadata = {}

    @property
    def is_expired(self) -> bool:
        """Check if token is expired."""
        if self.expires_at is None:
            return False

        import time

        return time.time() > self.expires_at


class TokenPort(ABC):
    """Generic token management port interface."""

    @abstractmethod
    async def create_token(
        self,
        user_id: str,
        client_id: Optional[str] = None,
        scope: List[str] = None,
        expires_in: Optional[int] = None,
        token_type: TokenType = TokenType.ACCESS,
        metadata: Dict[str, Any] = None,
    ) -> TokenResult:
        """
        Create a new token.

        Args:
            user_id: User identifier
            client_id: Client identifier (optional)
            scope: Token scope/permissions
            expires_in: Token expiration in seconds
            token_type: Type of token to create
            metadata: Additional token metadata

        Returns:
            Token creation result
        """

    @abstractmethod
    async def validate_token(self, token: str) -> TokenValidationResult:
        """
        Validate a token.

        Args:
            token: Token to validate

        Returns:
            Token validation result
        """

    @abstractmethod
    async def refresh_token(self, refresh_token: str) -> TokenResult:
        """
        Refresh an access token using a refresh token.

        Args:
            refresh_token: Refresh token

        Returns:
            New token result
        """

    @abstractmethod
    async def revoke_token(self, token: str) -> bool:
        """
        Revoke a token.

        Args:
            token: Token to revoke

        Returns:
            True if token was successfully revoked
        """

    @abstractmethod
    async def introspect_token(self, token: str) -> Dict[str, Any]:
        """
        Get detailed information about a token.

        Args:
            token: Token to introspect

        Returns:
            Token information dictionary
        """
