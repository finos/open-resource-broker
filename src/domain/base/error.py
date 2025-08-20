"""Domain error concepts - core error representation."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class DomainError:
    """
    Core domain error representation.

    This is the canonical error concept used across all layers.
    Immutable and focused on essential error information.
    """

    code: str
    message: str
    details: Dict[str, Any]
    category: str = "domain"

    def __post_init__(self) -> None:
        """Validate error data."""
        if not self.code:
            raise ValueError("Error code cannot be empty")
        if not self.message:
            raise ValueError("Error message cannot be empty")

    def with_detail(self, key: str, value: Any) -> "DomainError":
        """Create a new DomainError with additional detail."""
        new_details = {**self.details, key: value}
        return DomainError(
            code=self.code,
            message=self.message,
            details=new_details,
            category=self.category,
        )

    def with_category(self, category: str) -> "DomainError":
        """Create a new DomainError with different category."""
        return DomainError(
            code=self.code,
            message=self.message,
            details=self.details,
            category=category,
        )


@dataclass(frozen=True)
class ErrorContext:
    """Context information for error tracking."""

    timestamp: datetime
    operation: str
    layer: str
    request_id: Optional[str] = None
    user_id: Optional[str] = None

    @classmethod
    def create(cls, operation: str, layer: str, request_id: Optional[str] = None) -> "ErrorContext":
        """Create error context with current timestamp."""
        return cls(
            timestamp=datetime.utcnow(),
            operation=operation,
            layer=layer,
            request_id=request_id,
        )
