"""Authentication middleware for FastAPI."""

from typing import List

from fastapi import HTTPException, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware

from infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)
from infrastructure.logging.logger import get_logger


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware for FastAPI requests."""

    def __init__(
        self,
        app,
        auth_port: AuthPort,
        excluded_paths: List[str] = None,
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
        self.excluded_paths = excluded_paths or [
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/favicon.ico",
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
        # Skip authentication for excluded paths
        if self._is_excluded_path(request.url.path):
            self.logger.debug("Skipping auth for excluded path: %s", request.url.path)
            return await call_next(request)

        # Skip authentication if not required and auth is disabled
        if not self.require_auth and not self.auth_port.is_enabled():
            self.logger.debug("Authentication not required and disabled")
            return await call_next(request)

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

            self.logger.debug("Authentication successful for user: %s", auth_result.user_id)

            # Continue to next middleware/handler
            response = await call_next(request)

            # Add authentication headers to response if needed
            if auth_result.token:
                response.headers["X-Auth-Token"] = auth_result.token

            return response

        except Exception as e:
            self.logger.error("Authentication middleware error: %s", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication service error",
            ) from e

    def _is_excluded_path(self, path: str) -> bool:
        """
        Check if path is excluded from authentication.

        Args:
            path: Request path

        Returns:
            True if path is excluded
        """
        for excluded_path in self.excluded_paths:
            if path.startswith(excluded_path):
                return True
        return False

    def _create_auth_context(self, request: Request) -> AuthContext:
        """
        Create authentication context from request.

        Args:
            request: FastAPI request

        Returns:
            Authentication context
        """
        return AuthContext(
            method=request.method,
            path=request.url.path,
            headers=dict(request.headers),
            query_params=dict(request.query_params),
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            metadata={"url": str(request.url), "base_url": str(request.base_url)},
        )

    def _handle_auth_failure(self, auth_result: AuthResult) -> Response:
        """
        Handle authentication failure.

        Args:
            auth_result: Failed authentication result

        Returns:
            HTTP error response
        """
        if auth_result.status == AuthStatus.EXPIRED:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        elif auth_result.status == AuthStatus.INSUFFICIENT_PERMISSIONS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=auth_result.error_message or "Authentication failed",
                headers={"WWW-Authenticate": "Bearer"},
            )


class AuthDependency:
    """FastAPI dependency for authentication."""

    def __init__(
        self,
        required_permissions: List[str] = None,
        required_roles: List[str] = None,
        allow_service_accounts: bool = True,
    ) -> None:
        """
        Initialize auth dependency.

        Args:
            required_permissions: Required permissions for access
            required_roles: Required roles for access
            allow_service_accounts: Whether to allow service accounts
        """
        self.required_permissions = required_permissions or []
        self.required_roles = required_roles or []
        self.allow_service_accounts = allow_service_accounts
        self.logger = get_logger(__name__)

    def __call__(self, request: Request) -> AuthResult:
        """
        Dependency function for FastAPI.

        Args:
            request: FastAPI request with auth state

        Returns:
            Authentication result

        Raises:
            HTTPException: If authentication/authorization fails
        """
        # Get auth result from middleware
        auth_result = getattr(request.state, "auth_result", None)

        if not auth_result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        if not auth_result.is_authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed"
            )

        # Check required permissions
        for permission in self.required_permissions:
            if not auth_result.has_permission(permission):
                self.logger.warning(
                    "User %s missing permission: %s", auth_result.user_id, permission
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required permission: {permission}",
                )

        # Check required roles
        for role in self.required_roles:
            if not auth_result.has_role(role):
                self.logger.warning("User %s missing role: %s", auth_result.user_id, role)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required role: {role}",
                )

        return auth_result


# Common auth dependencies
def require_auth() -> AuthDependency:
    """Require basic authentication."""
    return AuthDependency()


def require_admin() -> AuthDependency:
    """Require admin role."""
    return AuthDependency(required_roles=["admin"])


def require_operator() -> AuthDependency:
    """Require operator role."""
    return AuthDependency(required_roles=["operator", "admin"])


def require_permissions(*permissions: str) -> AuthDependency:
    """Require specific permissions."""
    return AuthDependency(required_permissions=list(permissions))
