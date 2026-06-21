"""Redis-based token denylist implementation."""

import time
from typing import Optional

from orb.infrastructure.logging.logger import get_logger

from .denylist_port import TokenDenylistPort


class RedisTokenDenylist(TokenDenylistPort):
    """Redis-based token denylist with automatic expiration."""

    def __init__(self, redis_client=None, key_prefix: str = "token_denylist:") -> None:
        """
        Initialize Redis denylist.

        Args:
            redis_client: Redis client instance (optional, will use in-memory if None)
            key_prefix: Prefix for Redis keys
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._logger = get_logger(__name__)
        self._fallback = None

        if self._redis is None:
            self._logger.warning("Redis client not provided, using in-memory fallback")
            from .in_memory_denylist import InMemoryTokenDenylist

            self._fallback = InMemoryTokenDenylist()

    async def add_token(self, token: str, expires_at: Optional[int] = None) -> bool:
        """Add token to denylist."""
        if self._fallback:
            return await self._fallback.add_token(token, expires_at)

        try:
            key = f"{self._key_prefix}{token}"

            if expires_at:
                # Calculate TTL in seconds
                ttl = max(1, int(expires_at - time.time()))
                await self._redis.setex(key, ttl, "1")  # type: ignore[union-attr]
            else:
                # No expiration, set indefinitely
                await self._redis.set(key, "1")  # type: ignore[union-attr]

            self._logger.info("Token added to Redis denylist (expires_at=%s)", expires_at)
            return True

        except Exception as e:
            self._logger.error("Failed to add token to Redis denylist: %s", e)
            return False

    async def is_denylisted(self, token: str) -> bool:
        """Check if token is on the denylist."""
        if self._fallback:
            return await self._fallback.is_denylisted(token)

        try:
            key = f"{self._key_prefix}{token}"
            result = await self._redis.exists(key)  # type: ignore[union-attr]
            return bool(result)

        except Exception as e:
            self._logger.error("Failed to check token in Redis denylist: %s", e)
            # Fail secure: assume token is denylisted on error
            return True

    async def remove_token(self, token: str) -> bool:
        """Remove token from denylist."""
        if self._fallback:
            return await self._fallback.remove_token(token)

        try:
            key = f"{self._key_prefix}{token}"
            result = await self._redis.delete(key)  # type: ignore[union-attr]
            self._logger.info("Token removed from Redis denylist")
            return bool(result)

        except Exception as e:
            self._logger.error("Failed to remove token from Redis denylist: %s", e)
            return False

    async def cleanup_expired(self) -> int:
        """
        Remove expired tokens from denylist.

        Note: Redis automatically removes expired keys, so this is a no-op.
        """
        if self._fallback:
            return await self._fallback.cleanup_expired()

        # Redis handles expiration automatically
        return 0

    async def get_denylist_size(self) -> int:
        """Get number of tokens on the denylist."""
        if self._fallback:
            return await self._fallback.get_denylist_size()

        try:
            # Count keys matching our prefix
            pattern = f"{self._key_prefix}*"
            keys = await self._redis.keys(pattern)  # type: ignore[union-attr]
            return len(keys)

        except Exception as e:
            self._logger.error("Failed to get denylist size from Redis: %s", e)
            return 0
