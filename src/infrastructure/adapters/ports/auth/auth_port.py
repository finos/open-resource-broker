"""Generic authentication port interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AuthStatus(Enum):
    """Authentication status enumeration."""

    SUCCESS = "success"
    FAILED = "failed"
    EXPIRED = "expired"
    INVALID = "invalid"
    INSUFFICIENT_PERMISSIONS = "insufficient_permissions"


@dataclass
class AuthContext:
    """Authentication context containing request information."""

    # Request information
    method: str
    path: str
    headers: Dict[str, str]
    query_params: Dict[str, str]

    # Client information
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None

    # Additional context
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthResult:
    """Result of authentication attempt."""

    status: AuthStatus
    user_id: Optional[str] = None
    user_roles: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    token: Optional[str] = None
    expires_at: Optional[int] = None  # Unix timestamp
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.user_roles is None:
            self.user_roles = []
        if self.permissions is None:
            self.permissions = []
        if self.metadata is None:
            self.metadata = {}

    @property
    def is_authenticated(self) -> bool:
        """Check if authentication was successful."""
        return self.status == AuthStatus.SUCCESS

    @property
    def is_expired(self) -> bool:
        """Check if authentication is expired."""
        return self.status == AuthStatus.EXPIRED

    def has_permission(self, permission: str) -> bool:
        """Check if user has specific permission."""
        return permission in self.permissions

    def has_role(self, role: str) -> bool:
        """Check if user has specific role."""
        return role in self.user_roles


class AuthPort(ABC):
    """Generic authentication port interface."""

    @abstractmethod
    async def authenticate(self, context: AuthContext) -> AuthResult:
        """
        Authenticate a request based on the provided context.

        Args:
            context: Authentication context with request information

        Returns:
            Authentication result with user information and permissions
        """

    @abstractmethod
    async def validate_token(self, token: str) -> AuthResult:
        """
        Validate an authentication token.

        Args:
            token: Authentication token to validate

        Returns:
            Authentication result with validation status
        """

    @abstractmethod
    async def refresh_token(self, refresh_token: str) -> AuthResult:
        """
        Refresh an authentication token.

        Args:
            refresh_token: Refresh token to use for getting new access token

        Returns:
            Authentication result with new token information
        """

    @abstractmethod
    async def revoke_token(self, token: str) -> bool:
        """
        Revoke an authentication token.

        Args:
            token: Token to revoke

        Returns:
            True if token was successfully revoked
        """

    @abstractmethod
    def get_strategy_name(self) -> str:
        """
        Get the name of the authentication strategy.

        Returns:
            Strategy name (e.g., 'oauth', 'iam', 'none')
        """

    @abstractmethod
    def is_enabled(self) -> bool:
        """
        Check if authentication is enabled.

        Returns:
            True if authentication is enabled
        """
