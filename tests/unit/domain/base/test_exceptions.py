"""Tests for enhanced exception hierarchy with correlation IDs and to_dict()."""

import uuid
from typing import Any

import pytest

from domain.base.exceptions import (
    ApplicationError,
    BusinessRuleViolationError,
    CommandExecutionError,
    ConcurrencyError,
    ConfigurationError,
    ContainerNotInitializedError,
    DomainException,
    DuplicateError,
    EntityNotFoundError,
    HandlerNotFoundError,
    InitializationError,
    InfrastructureError,
    InvariantViolationError,
    QueryExecutionError,
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

    def test_with_correlation_id(self) -> None:
        """Test with correlation ID."""
        correlation_id = str(uuid.uuid4())
        exc = EntityNotFoundError("User", "user-123", correlation_id=correlation_id)
        assert exc.correlation_id == correlation_id


class TestCommandExecutionError:
    """Test CommandExecutionError."""

    def test_initialization(self) -> None:
        """Test command execution error initialization."""
        exc = CommandExecutionError("Command failed")
        assert exc.message == "Command failed"
        assert exc.error_code == "COMMAND_EXECUTION_FAILED"

    def test_with_command_name(self) -> None:
        """Test with command name."""
        exc = CommandExecutionError("Command failed", command_name="CreateUserCommand")
        assert exc.details["command_name"] == "CreateUserCommand"

    def test_with_details(self) -> None:
        """Test with additional details."""
        details = {"user_id": "123", "reason": "validation failed"}
        exc = CommandExecutionError(
            "Command failed",
            command_name="CreateUserCommand",
            details=details,
        )
        assert exc.details["command_name"] == "CreateUserCommand"
        assert exc.details["user_id"] == "123"
        assert exc.details["reason"] == "validation failed"


class TestQueryExecutionError:
    """Test QueryExecutionError."""

    def test_initialization(self) -> None:
        """Test query execution error initialization."""
        exc = QueryExecutionError("Query failed")
        assert exc.message == "Query failed"
        assert exc.error_code == "QUERY_EXECUTION_FAILED"

    def test_with_query_name(self) -> None:
        """Test with query name."""
        exc = QueryExecutionError("Query failed", query_name="GetUserQuery")
        assert exc.details["query_name"] == "GetUserQuery"


class TestHandlerNotFoundError:
    """Test HandlerNotFoundError."""

    def test_initialization(self) -> None:
        """Test handler not found error initialization."""
        exc = HandlerNotFoundError("Command", "CreateUserCommand")
        assert exc.message == "Command handler 'CreateUserCommand' not found"
        assert exc.error_code == "HANDLER_NOT_FOUND"
        assert exc.details == {
            "handler_type": "Command",
            "handler_name": "CreateUserCommand",
        }


class TestInitializationError:
    """Test InitializationError."""

    def test_initialization(self) -> None:
        """Test initialization error."""
        exc = InitializationError("Failed to initialize")
        assert exc.message == "Failed to initialize"
        assert exc.error_code == "INITIALIZATION_FAILED"

    def test_with_component(self) -> None:
        """Test with component name."""
        exc = InitializationError("Failed to initialize", component="database")
        assert exc.details["component"] == "database"


class TestContainerNotInitializedError:
    """Test ContainerNotInitializedError."""

    def test_initialization(self) -> None:
        """Test container not initialized error."""
        exc = ContainerNotInitializedError()
        assert exc.message == "DI container not initialized"
        assert exc.error_code == "INITIALIZATION_FAILED"
        assert exc.details["component"] == "container"

    def test_with_operation(self) -> None:
        """Test with operation name."""
        exc = ContainerNotInitializedError(operation="get_service")
        assert "get_service" in exc.message
        assert exc.details["operation"] == "get_service"


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

    def test_command_error_is_application_error(self) -> None:
        """Test CommandExecutionError inherits from ApplicationError."""
        exc = CommandExecutionError("Command failed")
        assert isinstance(exc, ApplicationError)
        assert isinstance(exc, DomainException)

    def test_query_error_is_application_error(self) -> None:
        """Test QueryExecutionError inherits from ApplicationError."""
        exc = QueryExecutionError("Query failed")
        assert isinstance(exc, ApplicationError)
        assert isinstance(exc, DomainException)

    def test_initialization_error_is_domain_exception(self) -> None:
        """Test InitializationError inherits from DomainException."""
        exc = InitializationError("Init failed")
        assert isinstance(exc, DomainException)

    def test_container_error_is_initialization_error(self) -> None:
        """Test ContainerNotInitializedError inherits from InitializationError."""
        exc = ContainerNotInitializedError()
        assert isinstance(exc, InitializationError)
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
                raise CommandExecutionError("Command failed") from e
        except CommandExecutionError as exc:
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
        wrapped = CommandExecutionError(
            "Command failed due to validation",
            correlation_id=original.correlation_id,
        )

        assert original.correlation_id == correlation_id
        assert wrapped.correlation_id == correlation_id
