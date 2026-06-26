"""Read-only mode middleware — rejects mutating requests when server.read_only=true."""

from typing import Any, Callable, Coroutine

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_ALLOWED_PATHS = frozenset(
    {
        "/health",
        "/ping",
        "/info",
        "/metrics",
        "/orb/health",
        "/orb/info",
        "/orb/metrics",
        "/_event",
        "/_upload",
    }
)


class ReadOnlyMiddleware(BaseHTTPMiddleware):
    """Reject mutating HTTP methods when read-only mode is active."""

    def __init__(self, app: Any, enabled: bool = False) -> None:
        super().__init__(app)
        self._enabled = enabled

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Coroutine[Any, Any, Response]],
    ) -> Response:
        if not self._enabled:
            return await call_next(request)

        if request.method in _SAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        # Allow exact matches and any path that starts with an allowlisted prefix
        # (e.g. /_event/... for Reflex websocket sub-paths).
        if path in _ALLOWED_PATHS or any(path.startswith(p + "/") for p in _ALLOWED_PATHS):
            return await call_next(request)

        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "error": {
                    "code": "READ_ONLY_MODE",
                    "message": "Server is in read-only mode.",
                },
            },
        )
