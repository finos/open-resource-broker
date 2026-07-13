"""
Tests for InfrastructureErrorResponse and _exception_to_components.

Verifies that sensitive data in exception messages and details never reaches
the caller via from_exception or _exception_to_components.
"""

from orb.domain.base.exceptions import (
    BusinessRuleViolationError,
    ConfigurationError,
    EntityNotFoundError,
    InfrastructureError,
    ValidationError,
)
from orb.infrastructure.error.responses import InfrastructureErrorResponse


class TestInfrastructureErrorResponseFromException:
    """Test InfrastructureErrorResponse.from_exception security properties."""

    def test_infrastructure_error_does_not_leak_message(self):
        """Regression (H3/HIGH): InfrastructureError message must not surface in response."""
        exception = InfrastructureError("host=secret-db.corp port=5432 password=abc")

        response = InfrastructureErrorResponse.from_exception(exception)

        assert "secret-db.corp" not in response.message
        assert "secret-db.corp" not in str(response.details)
        assert response.message == "An infrastructure error occurred"

    def test_configuration_error_does_not_leak_message(self):
        """Regression (H3): ConfigurationError message must not surface in response."""
        exception = ConfigurationError("secret_key=abc123 at /etc/orb/prod.yaml")

        response = InfrastructureErrorResponse.from_exception(exception)

        assert "secret_key" not in response.message
        assert "abc123" not in response.message
        assert "secret_key" not in str(response.details)
        assert response.message == "A configuration error occurred"

    def test_validation_error_does_not_leak_message(self):
        """Regression (H3): ValidationError message must not surface in response."""
        exception = ValidationError("field=internal_token must match pattern .*secret.*")

        response = InfrastructureErrorResponse.from_exception(exception)

        assert "internal_token" not in response.message
        assert response.message == "Invalid input"

    def test_entity_not_found_does_not_leak_message(self):
        """Regression (H3): EntityNotFoundError message must not surface in response."""
        exception = EntityNotFoundError("Machine", "m-secret-internal")

        response = InfrastructureErrorResponse.from_exception(exception)

        assert "m-secret-internal" not in response.message
        assert response.message == "Resource not found"

    def test_business_rule_violation_does_not_leak_message(self):
        """Regression (H3): BusinessRuleViolationError message must not surface."""
        exception = BusinessRuleViolationError("db_limit=500 exceeded for tenant=internal-acct")

        response = InfrastructureErrorResponse.from_exception(exception)

        assert "internal-acct" not in response.message
        assert response.message == "Request could not be processed"

    def test_infrastructure_error_details_are_empty(self):
        """Regression (H3): InfrastructureError details must be empty on the response."""
        exception = InfrastructureError(
            "conn failed",
            details={"original_error": "host=secret-db.corp:5432", "errno": 111},
        )

        response = InfrastructureErrorResponse.from_exception(exception)

        assert response.details == {}

    def test_configuration_error_details_are_empty(self):
        """Regression (H3): ConfigurationError details must be empty on the response."""
        exception = ConfigurationError(
            "bad config",
            details={
                "original_error": "key=db_password value=s3cr3t",
                "filename": "/etc/orb/config.yaml",
            },
        )

        response = InfrastructureErrorResponse.from_exception(exception)

        assert response.details == {}

    def test_validation_error_strips_unsafe_detail_keys(self):
        """Regression (H3): ValidationError details with unsafe keys must be filtered."""
        exception = ValidationError(
            "validation failed",
            details={
                "original_error": "host=secret.corp",
                "missing_key": "db_password",
                "field": "count",
                "field_name": "max_count",
            },
        )

        response = InfrastructureErrorResponse.from_exception(exception)

        assert "original_error" not in response.details
        assert "missing_key" not in response.details
        assert response.details.get("field") == "count"
        assert response.details.get("field_name") == "max_count"

    def test_context_parameter_not_forwarded_to_caller(self):
        """Regression (H3): from_exception context kwarg must not appear in response details."""
        exception = InfrastructureError("some infra failure")

        response = InfrastructureErrorResponse.from_exception(
            exception, context="internal-handler-v2"
        )

        assert "context" not in response.details
        assert "internal-handler-v2" not in str(response.details)
        assert "internal-handler-v2" not in response.message

    def test_unexpected_exception_returns_safe_message(self):
        """Regression: generic Exception must yield safe categorical message."""
        exception = RuntimeError("disk full at /data/orb/prod-primary")

        response = InfrastructureErrorResponse.from_exception(exception)

        assert "disk full" not in response.message
        assert "/data/orb/prod-primary" not in response.message
        assert response.message == "An unexpected error occurred"
        # Only exception type name is exposed, not message content
        assert response.details.get("exception_type") == "RuntimeError"
        assert "disk full" not in str(response.details)
