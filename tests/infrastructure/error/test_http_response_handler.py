"""
Tests for HTTPErrorResponseHandler.

Verifies HTTP error response formatting for all exception types.
"""

from http import HTTPStatus
from unittest.mock import MagicMock

import pytest

from orb.domain.base.exceptions import (
    BusinessRuleViolationError,
    ConcurrencyError,
    ConfigurationError,
    DuplicateError,
    EntityNotFoundError,
    InfrastructureError,
    ValidationError,
)
from orb.domain.machine.exceptions import (
    MachineNotFoundError,
    MachineValidationError,
)
from orb.domain.request.exceptions import (
    RequestNotFoundError,
    RequestValidationError,
)
from orb.domain.template.exceptions import (
    TemplateNotFoundError,
    TemplateValidationError,
)
from orb.infrastructure.error.categories import ErrorCategory, ErrorCode
from orb.infrastructure.error.http_response_handler import HTTPErrorResponseHandler


class TestHTTPErrorResponseHandler:
    """Test HTTP error response handler."""

    @pytest.fixture
    def handler(self):
        """Create HTTP error response handler."""
        return HTTPErrorResponseHandler()

    def test_validation_error_http(self, handler):
        """Test validation error HTTP response returns safe message, keeps field-level details."""
        exception = ValidationError(
            "Internal field path: user.secret_token", details={"field": "name"}
        )

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INVALID_INPUT
        assert response.message == "Invalid input"
        assert "Internal field path" not in response.message
        assert response.category == ErrorCategory.VALIDATION
        assert response.details == {"field": "name"}
        assert response.http_status == HTTPStatus.BAD_REQUEST

    def test_entity_not_found_error_http(self, handler):
        """Test entity not found error HTTP response."""
        exception = EntityNotFoundError("TestEntity", "123")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.RESOURCE_NOT_FOUND
        assert response.message == "Resource not found"
        assert response.category == ErrorCategory.NOT_FOUND
        assert response.details == {"entity_type": "TestEntity", "entity_id": "123"}
        assert response.http_status == HTTPStatus.NOT_FOUND

    def test_business_rule_violation_http(self, handler):
        """Test business rule violation HTTP response returns safe message, keeps rule details."""
        exception = BusinessRuleViolationError(
            "Internal rule detail: db_query_exceeded_limit", details={"rule": "max_count"}
        )

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.BUSINESS_RULE_VIOLATION
        assert response.message == "Request could not be processed"
        assert "Internal rule detail" not in response.message
        assert response.category == ErrorCategory.BUSINESS_RULE
        assert response.details == {"rule": "max_count"}
        assert response.http_status == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_request_not_found_http(self, handler):
        """Test request not found error HTTP response."""
        exception = RequestNotFoundError("req-123")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.REQUEST_NOT_FOUND
        assert response.message == "Request not found"
        assert response.category == ErrorCategory.NOT_FOUND
        assert response.details == {"entity_type": "Request", "entity_id": "req-123"}
        assert response.http_status == HTTPStatus.NOT_FOUND

    def test_request_validation_http(self, handler):
        """Test request validation error HTTP response returns safe message, keeps field details."""
        exception = RequestValidationError(
            "Invalid request: count must be <= 500", details={"field": "count"}
        )

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INVALID_INPUT
        assert response.message == "Invalid input"
        assert "count must be" not in response.message
        assert response.category == ErrorCategory.VALIDATION
        assert response.details == {"field": "count"}
        assert response.http_status == HTTPStatus.BAD_REQUEST

    def test_machine_not_found_http(self, handler):
        """Test machine not found error HTTP response."""
        exception = MachineNotFoundError("m-123")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.MACHINE_NOT_FOUND
        assert response.message == "Machine not found"
        assert response.category == ErrorCategory.NOT_FOUND
        assert response.details == {"entity_type": "Machine", "entity_id": "m-123"}
        assert response.http_status == HTTPStatus.NOT_FOUND

    def test_machine_validation_http(self, handler):
        """Test machine validation error HTTP response returns safe message, keeps field details."""
        exception = MachineValidationError(
            "Invalid machine: internal type code 0x3F", details={"field": "type"}
        )

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INVALID_INPUT
        assert response.message == "Invalid input"
        assert "internal type code" not in response.message
        assert response.category == ErrorCategory.VALIDATION
        assert response.details == {"field": "type"}
        assert response.http_status == HTTPStatus.BAD_REQUEST

    def test_template_not_found_http(self, handler):
        """Test template not found error HTTP response."""
        exception = TemplateNotFoundError("t-123")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.TEMPLATE_NOT_FOUND
        assert response.message == "Template not found"
        assert response.category == ErrorCategory.NOT_FOUND
        assert response.details == {"entity_type": "Template", "entity_id": "t-123"}
        assert response.http_status == HTTPStatus.NOT_FOUND

    def test_template_validation_http(self, handler):
        """Test template validation error HTTP response returns safe message, keeps field details."""
        exception = TemplateValidationError(
            "Invalid template: internal path /etc/templates/secret.yaml",
            details={"field": "config"},
        )

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INVALID_INPUT
        assert response.message == "Invalid input"
        assert "internal path" not in response.message
        assert response.category == ErrorCategory.VALIDATION
        assert response.details == {"field": "config"}
        assert response.http_status == HTTPStatus.BAD_REQUEST

    def test_infrastructure_error_http(self, handler):
        """Test infrastructure error HTTP response does not leak original_error to client."""
        exception = InfrastructureError(
            "Database connection failed: host=internal-db.corp port=5432"
        )

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.EXTERNAL_SERVICE_ERROR
        assert response.message == "An infrastructure error occurred"
        assert response.category == ErrorCategory.INFRASTRUCTURE
        assert "original_error" not in response.details
        assert "internal-db.corp" not in str(response.details)
        assert response.http_status == HTTPStatus.SERVICE_UNAVAILABLE

    def test_configuration_error_http(self, handler):
        """Test configuration error HTTP response does not leak original_error to client."""
        exception = ConfigurationError(
            "Invalid config: secret_key=abc123 at path /etc/orb/config.yaml"
        )

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INTERNAL_ERROR
        assert response.message == "A configuration error occurred"
        assert response.category == ErrorCategory.INTERNAL
        assert "original_error" not in response.details
        assert "secret_key" not in str(response.details)
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

    def test_concurrency_error_http(self, handler):
        """Test ConcurrencyError maps to HTTP 409 CONFLICT."""
        exception = ConcurrencyError(
            "Concurrent write detected for entity 'req-123'",
            details={"entity_id": "req-123", "expected_version": 2, "new_version": 3},
        )

        response = handler.handle_error_for_http(exception)

        assert response.error_code == "CONCURRENCY_ERROR"
        assert response.message == "The resource was modified concurrently; please retry."
        assert response.category == ErrorCategory.BUSINESS_RULE
        assert response.http_status == HTTPStatus.CONFLICT
        assert response.details == {
            "entity_id": "req-123",
            "expected_version": 2,
            "new_version": 3,
        }

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
        """Test exception without details attribute returns safe message with empty details."""
        exception = ValidationError("Simple validation error: internal detail xyz")

        response = handler.handle_error_for_http(exception)

        assert response.error_code == ErrorCode.INVALID_INPUT
        assert response.message == "Invalid input"
        assert "internal detail" not in response.message
        assert response.details == {}
        assert response.http_status == HTTPStatus.BAD_REQUEST

    def test_infrastructure_error_does_not_leak_secret_to_client_but_logs_it(self, handler):
        """Regression: InfrastructureError carrying a sensitive string must not surface it
        in the HTTP response body or details, but it must be logged server-side."""
        secret = "db_password=s3cr3t!@internal-host:5432/prod_db"
        exception = InfrastructureError(f"Connection error: {secret}")

        log_calls: list[tuple] = []

        original_logger = handler._logger

        mock_logger = MagicMock()
        mock_logger.error.side_effect = lambda *args, **kwargs: log_calls.append(
            ("error", args, kwargs)
        )
        handler._logger = mock_logger

        try:
            response = handler.handle_error_for_http(exception)
        finally:
            handler._logger = original_logger

        # The secret must NOT appear anywhere in the HTTP response
        assert secret not in response.message
        assert secret not in str(response.details)
        assert "original_error" not in response.details

        # The secret MUST have been forwarded to the logger
        assert len(log_calls) == 1
        logged_args = log_calls[0][1]
        assert any(secret in str(a) for a in logged_args)

    def test_duplicate_error_does_not_leak_raw_message(self, handler):
        """DuplicateError returns a generic safe message; raw detail stays out of response."""
        exception = DuplicateError("Duplicate key: machine_id=m-secret-prod-123 in table machines")

        response = handler.handle_error_for_http(exception)

        assert response.message == "A duplicate resource already exists"
        assert "m-secret-prod-123" not in response.message
        assert "m-secret-prod-123" not in str(response.details)

    # --- Regression tests ---

    def test_not_found_message_does_not_contain_entity_id(self, handler):
        """Regression (H3): NotFound response message must not echo the caller-supplied entity_id."""
        exception = EntityNotFoundError("Machine", "m-secret-internal-42")

        response = handler.handle_error_for_http(exception)

        assert "m-secret-internal-42" not in response.message

    def test_request_not_found_message_does_not_contain_entity_id(self, handler):
        """Regression (H3): RequestNotFoundError message must not echo the request_id."""
        exception = RequestNotFoundError("req-internal-007")

        response = handler.handle_error_for_http(exception)

        assert "req-internal-007" not in response.message

    def test_machine_not_found_message_does_not_contain_entity_id(self, handler):
        """Regression (H3): MachineNotFoundError message must not echo the machine_id."""
        exception = MachineNotFoundError("m-internal-xyz")

        response = handler.handle_error_for_http(exception)

        assert "m-internal-xyz" not in response.message

    def test_template_not_found_message_does_not_contain_entity_id(self, handler):
        """Regression (H3): TemplateNotFoundError message must not echo the template_id."""
        exception = TemplateNotFoundError("t-internal-abc")

        response = handler.handle_error_for_http(exception)

        assert "t-internal-abc" not in response.message

    def test_details_with_original_error_are_stripped(self, handler):
        """Regression (H3): details carrying original_error must not reach the response."""
        secret = "host=secret-internal-db.corp:5432"
        exception = ValidationError(
            "Wrapped error",
            details={
                "original_error": secret,
                "filename": "/etc/orb/config.yaml",
                "errno": 13,
                "missing_key": "db_password",
                "field": "count",
            },
        )

        response = handler.handle_error_for_http(exception)

        assert secret not in str(response.details)
        assert "original_error" not in response.details
        assert "filename" not in response.details
        assert "errno" not in response.details
        assert "missing_key" not in response.details
        # Safe field is preserved
        assert response.details.get("field") == "count"

    def test_unexpected_error_is_logged(self, handler):
        """Regression (H3): _handle_unexpected_error_http must log server-side."""
        exception = RuntimeError("disk full on /data/orb/prod")
        logged: list[tuple] = []

        original_logger = handler._logger
        mock_logger = MagicMock()
        mock_logger.error.side_effect = lambda *args, **kwargs: logged.append(("error", args))
        handler._logger = mock_logger

        try:
            response = handler.handle_error_for_http(exception)
        finally:
            handler._logger = original_logger

        # Client response must not contain the sensitive path
        assert "disk full" not in response.message
        assert "disk full" not in str(response.details)

        # Server-side log must have been called
        assert len(logged) >= 1, "Expected at least one logger.error call for unexpected errors"
