"""Rate-limit middleware for FastAPI — token-bucket per user/IP, no external deps."""

import asyncio
import logging
import math
import time
from collections import OrderedDict
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("orb.rate_limit")

_DEFAULT_REQUESTS_PER_MINUTE = 100
_DEFAULT_MAX_BUCKETS = 10_000


class _Bucket:
    """Token-bucket state for a single identity."""

    __slots__ = ("tokens", "last_refill")

    def __init__(self, capacity: float) -> None:
        self.tokens: float = capacity
        self.last_refill: float = time.monotonic()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory token-bucket rate limiter keyed on user_id (or client IP for anonymous).

    Configured via a ``rate_limiting`` dict (from ServerConfig.rate_limiting):
      - enabled (bool, default True)
      - requests_per_minute (int, default 100)

    When the bucket is empty the middleware returns HTTP 429 with a
    ``Retry-After`` header indicating seconds until the bucket refills enough
    for one request.

    Disabled entirely when the config dict is None or ``enabled`` is False.
    """

    def __init__(self, app, rate_limiting_config: Optional[dict[str, Any]] = None) -> None:
        super().__init__(app)
        cfg = rate_limiting_config or {}
        self._enabled: bool = bool(cfg.get("enabled", True)) if cfg else False
        rpm: int = int(cfg.get("requests_per_minute", _DEFAULT_REQUESTS_PER_MINUTE))
        # Capacity == burst == full minute's allowance; refill rate == tokens/second
        self._capacity: float = float(rpm)
        self._refill_rate: float = rpm / 60.0  # tokens per second
        # OrderedDict drives an LRU policy: most-recently-touched keys move
        # to the end on access; we evict from the front when over capacity.
        # Without this cap, a long-running server gets an unbounded dict as
        # client IPs / user_ids rotate (NAT churn, scanners, throwaway
        # tokens) — a slow memory leak.
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._max_buckets: int = int(cfg.get("max_buckets", _DEFAULT_MAX_BUCKETS))
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next):
        """Allow or reject the request based on the caller's token bucket."""
        if not self._enabled:
            return await call_next(request)

        identity = self._resolve_identity(request)
        allowed, retry_after = await self._check_and_consume(identity)

        if not allowed:
            logger.warning(
                "Rate limit exceeded for identity=%s path=%s method=%s retry_after=%ss",
                identity,
                request.url.path,
                request.method,
                retry_after,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": "Too many requests. Please slow down.",
                    },
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_identity(self, request: Request) -> str:
        """Return user_id when authenticated, otherwise fall back to client IP."""
        user_id: str = getattr(request.state, "user_id", "") or ""
        if user_id and user_id != "anonymous":
            return f"user:{user_id}"
        client_ip = request.client.host if request.client else "unknown"
        return f"ip:{client_ip}"

    async def _check_and_consume(self, identity: str) -> tuple[bool, int]:
        """Refill the bucket, then attempt to consume one token.

        Returns (allowed, retry_after_seconds).
        retry_after_seconds is 0 when allowed.
        """
        async with self._lock:
            now = time.monotonic()

            bucket = self._buckets.get(identity)
            if bucket is None:
                bucket = _Bucket(self._capacity)
                self._buckets[identity] = bucket
                # Evict the LRU entry once we exceed the cap. The bucket we
                # just inserted is the most-recent; we only ever drop one
                # entry per insertion, so the dict stays bounded.
                while len(self._buckets) > self._max_buckets:
                    self._buckets.popitem(last=False)
            else:
                # Touch — promote to most-recently-used.
                self._buckets.move_to_end(identity)

            # Refill proportional to elapsed time
            elapsed = now - bucket.last_refill
            bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_rate)
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0

            # Calculate seconds until one token is available
            tokens_needed = 1.0 - bucket.tokens
            retry_after = math.ceil(tokens_needed / self._refill_rate)
            return False, retry_after
