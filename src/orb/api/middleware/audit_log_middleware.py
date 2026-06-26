"""Audit log middleware for FastAPI — logs every mutating request with structured fields."""

import logging
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("orb.audit")


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Log every mutating request with structured fields.

    Logs at INFO level. Fields: ts, method, path, status_code, latency_ms,
    request_id, user_id, user_roles, client_ip, correlation_id. Skips safe
    verbs and health/metrics paths so logs aren't drowned.
    """

    SAFE_PATHS: frozenset[str] = frozenset(
        {"/health", "/ping", "/info", "/metrics", "/orb/health", "/orb/info", "/orb/metrics"}
    )
    SAFE_VERBS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

    async def dispatch(self, request: Request, call_next):
        """Process request; emit an audit log entry for mutating requests."""
        # Skip safe verbs and known health/metrics paths immediately
        if request.method in self.SAFE_VERBS or request.url.path in self.SAFE_PATHS:
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        latency_ms = round((time.monotonic() - start) * 1000, 2)

        # Pull fields that upstream middleware (LoggingMiddleware / AuthMiddleware) set
        request_id: str = getattr(request.state, "request_id", "")
        user_id: str = getattr(request.state, "user_id", "anonymous") or "anonymous"
        user_roles: list = getattr(request.state, "user_roles", []) or []
        # correlation_id may be forwarded by a gateway via X-Correlation-ID
        correlation_id: str = request.headers.get("x-correlation-id", request_id)
        client_ip: str = request.client.host if request.client else "unknown"

        logger.info(
            "audit",
            extra={
                "ts": time.time(),
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "request_id": request_id,
                "user_id": user_id,
                "user_roles": user_roles,
                "client_ip": client_ip,
                "correlation_id": correlation_id,
            },
        )

        return response
