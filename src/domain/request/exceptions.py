"""Request domain exceptions."""

from domain.base.exceptions import (
    DomainException,
    EntityNotFoundError,
    ValidationError,
)


class RequestException(DomainException):
    """Base exception for request domain errors."""


class RequestNotFoundError(EntityNotFoundError):
    """Raised when a request is not found."""

    def __init__(self, request_id: str):
        """Initialize the instance."""
        super().__init__("Request", request_id)


class RequestValidationError(ValidationError):
    """Raised when request validation fails."""


class InvalidRequestStateError(RequestException):
    """Raised when attempting an invalid request state transition."""

    def __init__(self, current_state: str, attempted_state: str):
        """Initialize request state transition error with states."""
        message = f"Cannot transition request from {current_state} to {attempted_state}"
        super().__init__(
            message,
            "INVALID_REQUEST_STATE_TRANSITION",
            {"current_state": current_state, "attempted_state": attempted_state},
        )


class RequestProcessingError(RequestException):
    """Raised when request processing fails."""


class RequestTimeoutError(RequestException):
    """Raised when request processing times out."""
