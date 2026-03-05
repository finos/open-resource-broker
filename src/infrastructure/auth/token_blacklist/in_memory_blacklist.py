"""In-memory token blacklist implementation."""

import asyncio
import time
from typing import Dict, Optional

from infrastructure.logging.logger import get_logger

from .blacklist_port import TokenBlacklistPort


class InMemoryTokenBlacklist(TokenBlacklistPort):
    """In-memory token blacklist with automatic cleanup."""

    def __init__(self, cleanup_interval: int = 3600) -> None:
        """
        Initialize in-memory blacklist.

        Args:
            cleanup_interval: Interval in seconds for automatic cleanup
        """
        self._blacklist: Dict[str, Optional[int]] = {}
        self._cleanup_interval = cleanup_interval
        self._cleanup_task: Optional[asyncio.Task] = None
        self._logger = get_logger(__name__)
        self._lock = asyncio.Lock()

    async def add_token(self, token: str, expires_at: Optional[int] = None) -> bool:
        """Add token to blacklist."""
        async with self._lock:
            self._blacklist[token] = expires_at
            self._logger.info("Token added to blacklist (expires_at=%s)", expires_at)
            return True

    async def is_blacklisted(self, token: str) -> bool:
        """Check if token is blacklisted."""
        async with self._lock:
            if token not in self._blacklist:
                return False

            # Check if token has expired
            expires_at = self._blacklist[token]
            if expires_at and time.time() > expires_at:
                # Token expired, remove from blacklist
                del self._blacklist[token]
                return False

            return True

    async def remove_token(self, token: str) -> bool:
        """Remove token from blacklist."""
        async with self._lock:
            if token in self._blacklist:
                del self._blacklist[token]
                self._logger.info("Token removed from blacklist")
                return True
            return False

    async def cleanup_expired(self) -> int:
        """Remove expired tokens from blacklist."""
        async with self._lock:
            current_time = time.time()
            expired_tokens = [
                token
                for token, expires_at in self._blacklist.items()
                if expires_at and current_time > expires_at
            ]

            for token in expired_tokens:
                del self._blacklist[token]

            if expired_tokens:
                self._logger.info("Cleaned up %d expired tokens", len(expired_tokens))

            return len(expired_tokens)

    async def get_blacklist_size(self) -> int:
        """Get number of tokens in blacklist."""
        async with self._lock:
            return len(self._blacklist)

    async def start_cleanup_task(self) -> None:
        """Start automatic cleanup task."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            self._logger.info("Started automatic cleanup task")

    async def stop_cleanup_task(self) -> None:
        """Stop automatic cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._logger.info("Stopped automatic cleanup task")

    async def _cleanup_loop(self) -> None:
        """Background task for periodic cleanup."""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self.cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error("Error in cleanup loop: %s", e)
