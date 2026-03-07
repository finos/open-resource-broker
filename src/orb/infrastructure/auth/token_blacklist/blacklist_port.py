"""Token blacklist port interface."""

from abc import ABC, abstractmethod
from typing import Optional


class TokenBlacklistPort(ABC):
    """Port interface for token blacklist implementations."""

    @abstractmethod
    async def add_token(self, token: str, expires_at: Optional[int] = None) -> bool:
        """
        Add token to blacklist.

        Args:
            token: Token to blacklist
            expires_at: Unix timestamp when token expires (for automatic cleanup)

        Returns:
            True if token was added successfully
        """

    @abstractmethod
    async def is_blacklisted(self, token: str) -> bool:
        """
        Check if token is blacklisted.

        Args:
            token: Token to check

        Returns:
            True if token is blacklisted
        """

    @abstractmethod
    async def remove_token(self, token: str) -> bool:
        """
        Remove token from blacklist.

        Args:
            token: Token to remove

        Returns:
            True if token was removed
        """

    @abstractmethod
    async def cleanup_expired(self) -> int:
        """
        Remove expired tokens from blacklist.

        Returns:
            Number of tokens removed
        """

    @abstractmethod
    async def get_blacklist_size(self) -> int:
        """
        Get number of tokens in blacklist.

        Returns:
            Number of blacklisted tokens
        """
