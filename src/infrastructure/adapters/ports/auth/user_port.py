"""User management port interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class UserRole(Enum):
    """User role enumeration."""

    ADMIN = "admin"
    USER = "user"
    VIEWER = "viewer"
    OPERATOR = "operator"
    SERVICE_ACCOUNT = "service_account"


@dataclass
class User:
    """User entity."""

    user_id: str
    username: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    roles: List[UserRole] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    is_active: bool = True
    is_service_account: bool = False
    created_at: Optional[int] = None  # Unix timestamp
    last_login: Optional[int] = None  # Unix timestamp
    metadata: Dict[str, Any] = field(default_factory=dict)

    def has_role(self, role: UserRole) -> bool:
        """Check if user has specific role."""
        return role in self.roles

    def has_permission(self, permission: str) -> bool:
        """Check if user has specific permission."""
        return permission in self.permissions

    def is_admin(self) -> bool:
        """Check if user is an admin."""
        return UserRole.ADMIN in self.roles


class UserPort(ABC):
    """Generic user management port interface."""

    @abstractmethod
    async def get_user(self, user_id: str) -> Optional[User]:
        """
        Get user by ID.

        Args:
            user_id: User identifier

        Returns:
            User object if found, None otherwise
        """

    @abstractmethod
    async def get_user_by_username(self, username: str) -> Optional[User]:
        """
        Get user by username.

        Args:
            username: Username

        Returns:
            User object if found, None otherwise
        """

    @abstractmethod
    async def get_user_by_email(self, email: str) -> Optional[User]:
        """
        Get user by email.

        Args:
            email: Email address

        Returns:
            User object if found, None otherwise
        """

    @abstractmethod
    async def create_user(self, user: User) -> bool:
        """
        Create a new user.

        Args:
            user: User to create

        Returns:
            True if user was created successfully
        """

    @abstractmethod
    async def update_user(self, user: User) -> bool:
        """
        Update an existing user.

        Args:
            user: User with updated information

        Returns:
            True if user was updated successfully
        """

    @abstractmethod
    async def delete_user(self, user_id: str) -> bool:
        """
        Delete a user.

        Args:
            user_id: User identifier

        Returns:
            True if user was deleted successfully
        """

    @abstractmethod
    async def list_users(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[User]:
        """
        List users with optional filtering and pagination.

        Args:
            limit: Maximum number of users to return
            offset: Number of users to skip
            filters: Filter criteria

        Returns:
            List of users
        """

    @abstractmethod
    async def authenticate_user(self, username: str, password: str) -> Optional[User]:
        """
        Authenticate user with username and password.

        Args:
            username: Username
            password: Password

        Returns:
            User object if authentication successful, None otherwise
        """
