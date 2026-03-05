"""
Tests for HTTPErrorResponseHandler.

Verifies HTTP error response formatting for all exception types.
"""

from http import HTTPStatus

import pytest

from domain.base.exceptions import (
    BusinessRuleViolationError,
    ConfigurationError,
    EntityNotFoundError,
    InfrastructureError,
    ValidationError,
)
from domain.machine.exceptions import (
    MachineNotFoundError,
    MachineValidationError,
)
from domain.request.exceptions import (
    RequestNotFoundError,
    RequestValidationError,
)
from domain.template.exceptions import (
    TemplateNotFoundError,
    TemplateValidationError,
)
from infrastructure.error.exception_handler import ErrorCategory, ErrorCode
from infrastructure.error.http_response_handler import HTTPErrorResponseHandler


class TestHTTPErrorResponseHandler:
    """Test HTTP error response handler."""

    @pytest.fixture
    def handler(self):
        """Create HTTP error response handler."""
        return HTTPErrorResponseHandler()

    def test_validation_error_http(self, handler):
        """Test validation error HTTP response."""
        exception = ValidationError("Invalid input", details={"field": "name"})

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INVALID_INPUT
        assert response.message == "Invalid input"
        assert response.category == ErrorCategory.VALIDATION
        assert response.details == {"field": "name"}
        assert response.http_status == HTTPStatus.BAD_REQUEST

    def test_entity_not_found_error_http(self, handler):
        """Test entity not found error HTTP response."""
        exception = EntityNotFoundError("TestEntity", "123")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.RESOURCE_NOT_FOUND
        assert response.message == "TestEntity with ID '123' not found"
        assert response.category == ErrorCategory.NOT_FOUND
        assert response.details == {"entity_type": "TestEntity", "entity_id": "123"}
        assert response.http_status == HTTPStatus.NOT_FOUND

    def test_business_rule_violation_http(self, handler):
        """Test business rule violation HTTP response."""
        exception = BusinessRuleViolationError("Rule violated", details={"rule": "max_count"})

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.BUSINESS_RULE_VIOLATION
        assert response.message == "Rule violated"
        assert response.category == ErrorCategory.BUSINESS_RULE
        assert response.details == {"rule": "max_count"}
        assert response.http_status == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_request_not_found_http(self, handler):
        """Test request not found error HTTP response."""
        exception = RequestNotFoundError("req-123")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.REQUEST_NOT_FOUND
        assert response.message == "Request with ID 'req-123' not found"
        assert response.category == ErrorCategory.NOT_FOUND
        assert response.details == {"entity_type": "Request", "entity_id": "req-123"}
        assert response.http_status == HTTPStatus.NOT_FOUND

    def test_request_validation_http(self, handler):
        """Test request validation error HTTP response."""
        exception = RequestValidationError("Invalid request", details={"field": "count"})

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INVALID_INPUT
        assert response.message == "Invalid request"
        assert response.category == ErrorCategory.VALIDATION
        assert response.details == {"field": "count"}
        assert response.http_status == HTTPStatus.BAD_REQUEST

    def test_machine_not_found_http(self, handler):
        """Test machine not found error HTTP response."""
        exception = MachineNotFoundError("m-123")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.MACHINE_NOT_FOUND
        assert response.message == "Machine with ID 'm-123' not found"
        assert response.category == ErrorCategory.NOT_FOUND
        assert response.details == {"entity_type": "Machine", "entity_id": "m-123"}
        assert response.http_status == HTTPStatus.NOT_FOUND

    def test_machine_validation_http(self, handler):
        """Test machine validation error HTTP response."""
        exception = MachineValidationError("Invalid machine", details={"field": "type"})

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INVALID_INPUT
        assert response.message == "Invalid machine"
        assert response.category == ErrorCategory.VALIDATION
        assert response.details == {"field": "type"}
        assert response.http_status == HTTPStatus.BAD_REQUEST

    def test_template_not_found_http(self, handler):
        """Test template not found error HTTP response."""
        exception = TemplateNotFoundError("t-123")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.TEMPLATE_NOT_FOUND
        assert response.message == "Template with ID 't-123' not found"
        assert response.category == ErrorCategory.NOT_FOUND
        assert response.details == {"entity_type": "Template", "entity_id": "t-123"}
        assert response.http_status == HTTPStatus.NOT_FOUND

    def test_template_validation_http(self, handler):
        """Test template validation error HTTP response."""
        exception = TemplateValidationError("Invalid template", details={"field": "config"})

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INVALID_INPUT
        assert response.message == "Invalid template"
        assert response.category == ErrorCategory.VALIDATION
        assert response.details == {"field": "config"}
        assert response.http_status == HTTPStatus.BAD_REQUEST

    def test_infrastructure_error_http(self, handler):
        """Test infrastructure error HTTP response."""
        exception = InfrastructureError("Database connection failed")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.EXTERNAL_SERVICE_ERROR
        assert response.message == "An infrastructure error occurred"
        assert response.category == ErrorCategory.INFRASTRUCTURE
        assert response.details == {"original_error": "Database connection failed"}
        assert response.http_status == HTTPStatus.SERVICE_UNAVAILABLE

    def test_configuration_error_http(self, handler):
        """Test configuration error HTTP response."""
        exception = ConfigurationError("Invalid config")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INTERNAL_ERROR
        assert response.message == "A configuration error occurred"
        assert response.category == ErrorCategory.INTERNAL
        assert response.details == {"original_error": "Invalid config"}
        assert response.http_status == HTTPStatus.INTERNAL_SERVER_ERROR

    def test_unexpected_error_http(self, handler):
        """Test unexpected error HTTP response."""
        exception = RuntimeError("Unexpected error")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.UNEXPECTED_ERROR
        assert response.message == "An unexpected error occurred"
        assert response.category == ErrorCategory.INTERNAL
        assert response.details == {"error_type": "RuntimeError"}
        assert response.http_status == HTTPStatus.INTERNAL_SERVER_ERROR

    def test_inheritance_handling(self, handler):
        """Test that inheritance hierarchy is handled correctly."""

        # Create a custom exception that inherits from ValidationError
        class CustomValidationError(ValidationError):
            pass

        exception = CustomValidationError("Custom validation error")

        response = handler.handle_error_for_http(exception)

        # Should use ValidationError handler due to inheritance
        assert response.error_code == ErrorCode.INVALID_INPUT
        assert response.category == ErrorCategory.VALIDATION
        assert response.http_status == HTTPStatus.BAD_REQUEST

    def test_exception_without_details(self, handler):
        """Test exception without details attribute."""
        exception = ValidationError("Simple validation error")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INVALID_INPUT
        assert response.message == "Simple validation error"
        assert response.details == {}
        assert response.http_status == HTTPStatus.BAD_REQUEST
