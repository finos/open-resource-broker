"""Logging middleware for FastAPI."""

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from infrastructure.logging.logger import get_logger


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logging middleware for FastAPI requests."""

    def __init__(self, app, log_requests: bool = True, log_responses: bool = True):
        """
        Initialize logging middleware.

        Args:
            app: FastAPI application
            log_requests: Whether to log requests
            log_responses: Whether to log responses
        """
        super().__init__(app)
        self.log_requests = log_requests
        self.log_responses = log_responses
        self.logger = get_logger(__name__)

    async def dispatch(self, request: Request, call_next):
        """
        Process request through logging middleware.

        Args:
            request: FastAPI request
            call_next: Next middleware/handler in chain

        Returns:
            Response from downstream handlers
        """
        # Generate request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Log request
        start_time = time.time()
        if self.log_requests:
            self._log_request(request, request_id)

        try:
            # Process request
            response = await call_next(request)

            # Log response
            duration = time.time() - start_time
            if self.log_responses:
                self._log_response(request, response, request_id, duration)

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            duration = time.time() - start_time
            self._log_error(request, e, request_id, duration)
            raise

    def _log_request(self, request: Request, request_id: str):
        """Log incoming request."""
        user_id = getattr(request.state, "user_id", "anonymous")
        client_ip = request.client.host if request.client else "unknown"

        self.logger.info(
            "Request %s: %s %s ", request_id, request.method, request.url.path
            f"from {client_ip} (user: {user_id})"
        )

        # Log query parameters if present
        if request.query_params:
            self.logger.debug("Request %s query params: %s", request_id, dict(request.query_params))

    def _log_response(self, request: Request, response: Response, request_id: str, duration: float):
        """Log outgoing response."""
        user_id = getattr(request.state, "user_id", "anonymous")

        self.logger.info(
            "Response %s: %s ", request_id, response.status_code
            f"for {request.method} {request.url.path} "
            f"(user: {user_id}, duration: {duration:.3f}s)"
        )

    def _log_error(self, request: Request, error: Exception, request_id: str, duration: float):
        """Log request error."""
        user_id = getattr(request.state, "user_id", "anonymous")

        self.logger.error(
            "Error %s: %s: %s ", request_id, type(error).__name__, str(error)
            f"for {request.method} {request.url.path} "
            f"(user: {user_id}, duration: {duration:.3f}s)"
        )
