"""Authentication middleware for FastAPI."""

import os
from typing import Optional

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from orb.infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)
from orb.infrastructure.logging.logger import get_logger


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware with security hardening."""

    def __init__(
        self,
        app,
        auth_port: AuthPort,
        excluded_paths: Optional[list[str]] = None,
        require_auth: bool = True,
    ) -> None:
        """
        Initialize authentication middleware.

        Args:
            app: FastAPI application
            auth_port: Authentication port implementation
            excluded_paths: Paths that don't require authentication
            require_auth: Whether authentication is required by default
        """
        super().__init__(app)
        self.auth_port = auth_port
        # Normalize excluded paths (remove trailing slashes, convert to lowercase)
        self.excluded_paths = [
            self._normalize_path(p)
            for p in (
                excluded_paths
                or [
                    "/health",
                    "/docs",
                    "/redoc",
                    "/openapi.json",
                    "/favicon.ico",
                ]
            )
        ]
        self.require_auth = require_auth
        self.logger = get_logger(__name__)

    async def dispatch(self, request: Request, call_next):
        """
        Process request through authentication middleware.

        Args:
            request: FastAPI request
            call_next: Next middleware/handler in chain

        Returns:
            Response from downstream handlers
        """
        # Normalize request path for comparison
        normalized_path = self._normalize_path(request.url.path)

        # Skip authentication for excluded paths (exact match only)
        if self._is_excluded_path(normalized_path):
            self.logger.debug("Skipping auth for excluded path: %s", request.url.path)
            response = await call_next(request)
            return self._add_security_headers(response)

        # Skip authentication if not required and auth is disabled
        if not self.require_auth and not self.auth_port.is_enabled():
            self.logger.debug("Authentication not required and disabled")
            response = await call_next(request)
            return self._add_security_headers(response)

        try:
            # Create authentication context
            auth_context = self._create_auth_context(request)

            # Perform authentication
            auth_result = await self.auth_port.authenticate(auth_context)

            # Handle authentication result
            if not auth_result.is_authenticated:
                return self._handle_auth_failure(auth_result)

            # Add authentication info to request state
            request.state.auth_result = auth_result
            request.state.user_id = auth_result.user_id
            request.state.user_roles = auth_result.user_roles
            request.state.permissions = auth_result.permissions

            self.logger.info(
                "Authentication successful for user: %s from IP: %s",
                auth_result.user_id,
                auth_context.client_ip,
            )

            # Continue to next middleware/handler
            response = await call_next(request)

            # Add security headers
            response = self._add_security_headers(response)

            # Add authentication headers to response if needed
            if auth_result.token:
                response.headers["X-Auth-Token"] = auth_result.token

            return response

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error("Authentication middleware error: %s", e, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error",
            )

    def _normalize_path(self, path: str) -> str:
        """
        Normalize path for secure comparison.

        Args:
            path: Request path

        Returns:
            Normalized path
        """
        # Remove trailing slashes
        normalized = path.rstrip("/")
        # Convert to lowercase for case-insensitive comparison
        normalized = normalized.lower()
        # Resolve path traversal attempts
        normalized = os.path.normpath(normalized)
        # Remove any remaining .. or . components
        parts = [p for p in normalized.split("/") if p and p not in (".", "..")]
        return "/" + "/".join(parts) if parts else "/"

    def _is_excluded_path(self, normalized_path: str) -> bool:
        """
        Check if path is excluded from authentication (exact match only).

        Args:
            normalized_path: Normalized request path

        Returns:
            True if path is excluded
        """
        # Exact match only - no prefix matching to prevent path traversal
        return normalized_path in self.excluded_paths

    def _create_auth_context(self, request: Request) -> AuthContext:
        """
        Create authentication context from request.

        Args:
            request: FastAPI request

        Returns:
            Authentication context
        """
        # Sanitize headers to prevent header injection
        sanitized_headers = {
            k.lower(): str(v)[:1000]  # Limit header value length
            for k, v in request.headers.items()
        }

        return AuthContext(
            method=request.method,
            path=request.url.path,
            headers=sanitized_headers,
            query_params=dict(request.query_params),
            client_ip=self._get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:500],  # Limit UA length
            metadata={
                "url": str(request.url)[:2000],  # Limit URL length
                "base_url": str(request.base_url)[:2000],
            },
        )

    def _get_client_ip(self, request: Request) -> Optional[str]:
        """
        Get client IP address with proxy support.

        Args:
            request: FastAPI request

        Returns:
            Client IP address
        """
        # Check X-Forwarded-For header (if behind proxy)
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # Take first IP (client IP)
            return forwarded_for.split(",")[0].strip()

        # Fall back to direct client IP
        return request.client.host if request.client else None

    def _handle_auth_failure(self, auth_result: AuthResult) -> Response:
        """
        Handle authentication failure with sanitized error messages.

        Args:
            auth_result: Failed authentication result

        Returns:
            HTTP error response
        """
        # Log detailed error internally
        self.logger.warning(
            "Authentication failed: status=%s, error=%s",
            auth_result.status,
            auth_result.error_message,
        )

        # Return generic error messages to prevent information disclosure
        if auth_result.status == AuthStatus.INSUFFICIENT_PERMISSIONS:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Access denied"},
            )
        elif auth_result.status == AuthStatus.EXPIRED:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication expired"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        else:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid credentials"},
                headers={"WWW-Authenticate": "Bearer"},
            )

    def _add_security_headers(self, response: Response) -> Response:
        """
        Add security headers to response.

        Args:
            response: Response object

        Returns:
            Response with security headers
        """
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Enable XSS protection
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Strict Transport Security (HTTPS only)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Content Security Policy
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )

        # Referrer Policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions Policy
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )

        return response
