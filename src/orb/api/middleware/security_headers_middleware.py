"""Security headers middleware — unconditionally adds hardening headers to every response."""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security-hardening headers to every HTTP response.

    This middleware is registered unconditionally in server.py so that all
    responses — including those on excluded auth paths and when auth is disabled
    — carry the full set of security headers.

    Args:
        app: The ASGI application to wrap.
        require_https: When True, emit the Strict-Transport-Security header.
            Must only be set when the server is actually serving TLS so
            browsers do not cache an HSTS policy for an HTTP-only origin.
    """

    def __init__(self, app, require_https: bool = False) -> None:
        super().__init__(app)
        self.require_https = require_https

    async def dispatch(self, request: Request, call_next) -> Response:
        """Pass the request downstream and attach security headers to the response."""
        response = await call_next(request)
        return self._add_security_headers(response)

    def _add_security_headers(self, response: Response) -> Response:
        """Attach security headers to *response* and return it.

        Args:
            response: A Starlette/FastAPI ``Response`` instance.

        Returns:
            The same response object with headers mutated in place.
        """
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # X-XSS-Protection is deprecated and removed from modern browsers; omitted
        # intentionally to avoid confusing legacy UAs into enabling a broken parser.

        # Strict-Transport-Security must only be emitted over HTTPS connections.
        # Sending HSTS on a plain-HTTP origin causes browsers to cache an upgrade
        # policy for a site that cannot serve TLS, breaking all future connections.
        if self.require_https:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Content Security Policy — 'unsafe-inline' removed from script-src.
        # If inline scripts are genuinely needed, use nonce-based CSP instead.
        # 'unsafe-inline' is kept for style-src because Reflex injects inline styles.
        # connect-src includes ws: and wss: to allow the Reflex /_event WebSocket
        # to reach a backend that may be on a different host in split-mode deployments.
        # WS connections are still protected by the browser's same-origin handshake
        # and Reflex's own origin check at the WS upgrade stage.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'"
        )

        # Referrer Policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions Policy
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )

        return response
