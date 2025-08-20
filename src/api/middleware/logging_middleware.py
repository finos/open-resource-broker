"""Logging middleware for FastAPI."""

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from infrastructure.logging.logger import get_logger


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logging middleware for FastAPI requests."""

    def __init__(self, app, log_requests: bool = True, log_responses: bool = True) -> None:
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

    def _log_request(self, request: Request, request_id: str) -> None:
        """Log incoming request."""
        user_id = getattr(request.state, "user_id", "anonymous")
        client_ip = request.client.host if request.client else "unknown"

        self.logger.info(
            "Request %s: %s %s from %s (user: %s)",
            request_id,
            request.method,
            request.url.path,
            client_ip,
            user_id,
        )

        # Log query parameters if present
        if request.query_params:
            self.logger.debug("Request %s query params: %s", request_id, dict(request.query_params))

    def _log_response(
        self, request: Request, response: Response, request_id: str, duration: float
    ) -> None:
        """Log outgoing response."""
        user_id = getattr(request.state, "user_id", "anonymous")

        self.logger.info(
            "Response %s: %s for %s %s (user: %s, duration: %.3fs)",
            request_id,
            response.status_code,
            request.method,
            request.url.path,
            user_id,
            duration,
        )

    def _log_error(
        self, request: Request, error: Exception, request_id: str, duration: float
    ) -> None:
        """Log request error."""
        user_id = getattr(request.state, "user_id", "anonymous")

        self.logger.error(
            "Error %s: %s: %s for %s %s (user: %s, duration: %.3fs)",
            request_id,
            type(error).__name__,
            str(error),
            request.method,
            request.url.path,
            user_id,
            duration,
        )
