"""Base domain exceptions - foundation for domain error handling."""

from typing import Any, Dict, Optional


class DomainException(Exception):
    """Base exception for all domain errors."""

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize domain exception with message, error code, and details."""
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}


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
