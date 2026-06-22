"""Token denylist port interface."""

from abc import ABC, abstractmethod
from typing import Optional


class TokenDenylistPort(ABC):
    """Port interface for token denylist implementations."""

    @abstractmethod
    async def add_token(self, token: str, expires_at: Optional[int] = None) -> bool:
        """
        Add token to denylist.

        Args:
            token: Token to add to the denylist
            expires_at: Unix timestamp when token expires (for automatic cleanup)

        Returns:
            True if token was added successfully
        """

    @abstractmethod
    async def is_denylisted(self, token: str) -> bool:
        """
        Check if token is on the denylist.

        Args:
            token: Token to check

        Returns:
            True if token is on the denylist
        """

    @abstractmethod
    async def remove_token(self, token: str) -> bool:
        """
        Remove token from denylist.

        Args:
            token: Token to remove

        Returns:
            True if token was removed
        """

    @abstractmethod
    async def cleanup_expired(self) -> int:
        """
        Remove expired tokens from denylist.

        Returns:
            Number of tokens removed
        """

    @abstractmethod
    async def get_denylist_size(self) -> int:
        """
        Get number of tokens on the denylist.

        Returns:
            Number of denylisted tokens
        """
