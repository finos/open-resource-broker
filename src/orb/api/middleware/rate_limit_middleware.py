"""Rate-limit middleware for FastAPI — token-bucket per user/IP, no external deps."""

import asyncio
import logging
import math
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Optional, Union

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from orb.api.middleware._utils import get_real_client_ip

if TYPE_CHECKING:
    from orb.config.schemas.server_schema import RateLimitConfig

logger = logging.getLogger("orb.rate_limit")

_DEFAULT_REQUESTS_PER_MINUTE = 100
_DEFAULT_MAX_BUCKETS = 10_000


class _Bucket:
    """Token-bucket state for a single identity."""

    __slots__ = ("last_refill", "tokens")

    def __init__(self, capacity: float) -> None:
        self.tokens: float = capacity
        self.last_refill: float = time.monotonic()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory token-bucket rate limiter keyed on user_id (or client IP for anonymous).

    Configured via a ``RateLimitConfig`` instance or a legacy dict (from
    ServerConfig.rate_limiting):
      - enabled (bool, default True)
      - requests_per_minute (int, default 100)
      - max_buckets (int, default 10 000)

    When the bucket is empty the middleware returns HTTP 429 with a
    ``Retry-After`` header indicating seconds until the bucket refills enough
    for one request.

    Disabled entirely when ``enabled`` is False.
    """

    def __init__(
        self,
        app,
        rate_limiting_config: Optional[Union["RateLimitConfig", dict[str, Any]]] = None,
        trusted_proxies: Optional[list[str]] = None,
    ) -> None:
        super().__init__(app)
        self._trusted_proxies: frozenset[str] = frozenset(trusted_proxies or [])
        # Accept either a typed RateLimitConfig or the legacy dict form so the
        # middleware is not hard-coupled to the schema import at middleware load time.
        if rate_limiting_config is None:
            self._enabled = False
            self._capacity = float(_DEFAULT_REQUESTS_PER_MINUTE)
            self._burst = float(_DEFAULT_REQUESTS_PER_MINUTE)
            self._refill_rate = _DEFAULT_REQUESTS_PER_MINUTE / 60.0
            self._max_buckets = _DEFAULT_MAX_BUCKETS
        elif isinstance(rate_limiting_config, dict):
            cfg = rate_limiting_config
            self._enabled = bool(cfg.get("enabled", True))
            rpm = int(cfg.get("requests_per_minute", _DEFAULT_REQUESTS_PER_MINUTE))
            self._capacity = float(rpm)
            self._burst = float(cfg.get("burst", rpm))
            self._refill_rate = rpm / 60.0
            self._max_buckets = int(cfg.get("max_buckets", _DEFAULT_MAX_BUCKETS))
        else:
            # Typed RateLimitConfig object — access via attribute.
            self._enabled = bool(rate_limiting_config.enabled)
            rpm = int(rate_limiting_config.requests_per_minute)
            self._capacity = float(rpm)
            burst = getattr(rate_limiting_config, "burst", rpm)
            self._burst = float(burst)
            self._refill_rate = rpm / 60.0
            self._max_buckets = int(rate_limiting_config.max_buckets)
        # OrderedDict drives an LRU policy: most-recently-touched keys move
        # to the end on access; we evict from the front when over capacity.
        # Without this cap, a long-running server gets an unbounded dict as
        # client IPs / user_ids rotate (NAT churn, scanners, throwaway
        # tokens) — a slow memory leak.
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
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
        """Return user_id when authenticated, otherwise fall back to client IP.

        Uses ``get_real_client_ip`` so that when a trusted proxy is configured
        the rate-limiter keys on the originating client IP rather than the
        proxy's IP, preventing all traffic through that proxy from sharing a
        single bucket.
        """
        user_id: str = getattr(request.state, "user_id", "") or ""
        if user_id and user_id != "anonymous":
            return f"user:{user_id}"
        client_ip = get_real_client_ip(request, self._trusted_proxies) or "unknown"
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
                bucket = _Bucket(self._burst)
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
