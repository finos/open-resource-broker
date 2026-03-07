"""Base domain exceptions - foundation for domain error handling."""

import uuid
from typing import Any, Optional


class DomainException(Exception):
    """Base exception for all domain errors."""

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Initialize domain exception with message, error code, and details."""
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}
        self.correlation_id = correlation_id or str(uuid.uuid4())

    def to_dict(self) -> dict[str, Any]:
        """
        Convert exception to dictionary for structured logging and API responses.

        Returns:
            Dictionary representation of exception
        """
        return {
            "error_type": self.__class__.__name__,
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
            "correlation_id": self.correlation_id,
        }


class ValidationError(DomainException):
    """Raised when domain validation fails."""


class BusinessRuleViolationError(DomainException):
    """Raised when a business rule is violated."""


# Alias for backward compatibility
BusinessRuleError = BusinessRuleViolationError


class EntityNotFoundError(DomainException):
    """Raised when an entity is not found."""

    def __init__(self, entity_type: str, entity_id: str) -> None:
        """Initialize entity not found error with type and ID."""
        message = f"{entity_type} with ID '{entity_id}' not found"
        super().__init__(
            message,
            "ENTITY_NOT_FOUND",
            {"entity_type": entity_type, "entity_id": entity_id},
        )


class ConcurrencyError(DomainException):
    """Raised when a concurrency conflict occurs."""


class InvariantViolationError(DomainException):
    """Raised when a domain invariant is violated."""


class DuplicateError(DomainException):
    """Raised when attempting to create a duplicate resource."""


class InfrastructureError(DomainException):
    """Raised when infrastructure operations fail."""


class ConfigurationError(DomainException):
    """Raised when configuration is invalid."""


class ApplicationError(DomainException):
    """Raised when application layer operations fail."""


class QuotaError(DomainException):
    """Raised when a provider quota or limit is exceeded."""


class QuotaExceededError(QuotaError):
    """Raised when a specific provider quota is exceeded."""
