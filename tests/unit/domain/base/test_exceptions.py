"""Tests for domain exception hierarchy with correlation IDs and to_dict()."""

import uuid

from orb.domain.base.exceptions import (
    ApplicationError,
    DomainException,
    EntityNotFoundError,
    ValidationError,
)


class TestDomainException:
    """Test base DomainException functionality."""

    def test_basic_initialization(self) -> None:
        """Test basic exception initialization."""
        exc = DomainException("Test error")
        assert exc.message == "Test error"
        assert exc.error_code == "DomainException"
        assert exc.details == {}
        assert exc.correlation_id is not None
        assert isinstance(exc.correlation_id, str)

    def test_with_error_code(self) -> None:
        """Test exception with custom error code."""
        exc = DomainException("Test error", error_code="CUSTOM_ERROR")
        assert exc.error_code == "CUSTOM_ERROR"

    def test_with_details(self) -> None:
        """Test exception with details."""
        details = {"key": "value", "count": 42}
        exc = DomainException("Test error", details=details)
        assert exc.details == details

    def test_with_correlation_id(self) -> None:
        """Test exception with custom correlation ID."""
        correlation_id = str(uuid.uuid4())
        exc = DomainException("Test error", correlation_id=correlation_id)
        assert exc.correlation_id == correlation_id

    def test_to_dict(self) -> None:
        """Test to_dict() method."""
        correlation_id = str(uuid.uuid4())
        details = {"key": "value"}
        exc = DomainException(
            "Test error",
            error_code="TEST_ERROR",
            details=details,
            correlation_id=correlation_id,
        )
        result = exc.to_dict()
        assert result == {
            "error_type": "DomainException",
            "error_code": "TEST_ERROR",
            "message": "Test error",
            "details": details,
            "correlation_id": correlation_id,
        }


class TestEntityNotFoundError:
    """Test EntityNotFoundError."""

    def test_initialization(self) -> None:
        """Test entity not found error initialization."""
        exc = EntityNotFoundError("User", "user-123")
        assert exc.message == "User with ID 'user-123' not found"
        assert exc.error_code == "ENTITY_NOT_FOUND"
        assert exc.details == {"entity_type": "User", "entity_id": "user-123"}


class TestExceptionHierarchy:
    """Test exception hierarchy relationships."""

    def test_validation_error_is_domain_exception(self) -> None:
        """Test ValidationError inherits from DomainException."""
        exc = ValidationError("Invalid input")
        assert isinstance(exc, DomainException)

    def test_application_error_is_domain_exception(self) -> None:
        """Test ApplicationError inherits from DomainException."""
        exc = ApplicationError("App error")
        assert isinstance(exc, DomainException)


class TestExceptionChaining:
    """Test exception chaining with 'from' clause."""

    def test_exception_chaining(self) -> None:
        """Test that exceptions can be chained properly."""
        original = ValueError("Original error")
        try:
            try:
                raise original
            except ValueError as e:
                raise ApplicationError("Application failed") from e
        except ApplicationError as exc:
            assert exc.__cause__ is original
            assert str(exc.__cause__) == "Original error"


class TestCorrelationIdPropagation:
    """Test correlation ID propagation across exception types."""

    def test_correlation_id_propagation(self) -> None:
        """Test correlation ID can be propagated across exceptions."""
        correlation_id = str(uuid.uuid4())

        # Create original exception with correlation ID
        original = ValidationError(
            "Validation failed",
            correlation_id=correlation_id,
        )

        # Create new exception with same correlation ID
        wrapped = ApplicationError(
            "Application failed due to validation",
            correlation_id=original.correlation_id,
        )

        assert original.correlation_id == correlation_id
        assert wrapped.correlation_id == correlation_id
