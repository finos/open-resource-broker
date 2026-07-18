"""Unit tests for error/responses.py."""

from http import HTTPStatus

import pytest

from orb.domain.base.exceptions import (
    BusinessRuleViolationError,
    ConfigurationError,
    EntityNotFoundError,
    InfrastructureError,
    ValidationError,
)
from orb.infrastructure.error.categories import ErrorCategory
from orb.infrastructure.error.responses import (
    InfrastructureErrorResponse,
    _safe_details,
)


@pytest.mark.unit
class TestSafeDetails:
    """Tests for the _safe_details helper."""

    def test_safe_keys_are_forwarded(self) -> None:
        raw = {
            "entity_type": "Machine",
            "entity_id": "m-1",
            "field": "name",
            "field_name": "label",
            "rule": "unique",
            "expected_version": 1,
            "new_version": 2,
            "current_state": "running",
            "attempted_state": "stopped",
        }
        result = _safe_details(raw)
        assert result == raw

    def test_unsafe_keys_are_stripped(self) -> None:
        raw = {
            "original_error": "secret host info",
            "errno": 22,
            "filename": "/internal/path",
            "entity_type": "Machine",
        }
        result = _safe_details(raw)
        assert result == {"entity_type": "Machine"}
        assert "original_error" not in result
        assert "errno" not in result
        assert "filename" not in result

    def test_empty_dict_returns_empty(self) -> None:
        assert _safe_details({}) == {}

    def test_all_unsafe_returns_empty(self) -> None:
        raw = {"original_error": "x", "stacktrace": "y", "hostname": "z"}
        assert _safe_details(raw) == {}


@pytest.mark.unit
class TestInfrastructureErrorResponseFromDomainError:
    """Tests for InfrastructureErrorResponse.from_domain_error."""

    def test_basic_creation(self) -> None:
        resp = InfrastructureErrorResponse.from_domain_error(
            error_code="ERR_001",
            message="Something went wrong",
        )
        assert resp.error_code == "ERR_001"
        assert resp.message == "Something went wrong"
        assert resp.category == ErrorCategory.INTERNAL
        assert resp.details == {}
        assert resp.http_status == HTTPStatus.INTERNAL_SERVER_ERROR

    def test_explicit_http_status_is_respected(self) -> None:
        resp = InfrastructureErrorResponse.from_domain_error(
            error_code="NOT_FOUND",
            message="Not found",
            category=ErrorCategory.ENTITY_NOT_FOUND,
            http_status=HTTPStatus.NOT_FOUND,
        )
        assert resp.http_status == HTTPStatus.NOT_FOUND

    def test_category_drives_http_status_when_not_specified(self) -> None:
        resp = InfrastructureErrorResponse.from_domain_error(
            error_code="VALIDATION",
            message="Bad input",
            category=ErrorCategory.VALIDATION,
        )
        assert resp.http_status == HTTPStatus.BAD_REQUEST

    def test_details_passed_through(self) -> None:
        resp = InfrastructureErrorResponse.from_domain_error(
            error_code="X",
            message="y",
            details={"entity_type": "Request"},
        )
        assert resp.details == {"entity_type": "Request"}

    def test_details_default_to_empty(self) -> None:
        resp = InfrastructureErrorResponse.from_domain_error(
            error_code="X",
            message="y",
        )
        assert resp.details == {}


@pytest.mark.unit
class TestInfrastructureErrorResponseFromException:
    """Tests for InfrastructureErrorResponse.from_exception (all exception branches)."""

    def test_validation_error(self) -> None:
        exc = ValidationError("bad value", details={"field": "name"})
        resp = InfrastructureErrorResponse.from_exception(exc)
        assert resp.error_code == "VALIDATION_ERROR"
        assert resp.message == "Invalid input"
        assert resp.category == ErrorCategory.VALIDATION
        assert resp.http_status == HTTPStatus.BAD_REQUEST
        # Safe detail forwarded
        assert resp.details.get("field") == "name"

    def test_entity_not_found_error(self) -> None:
        exc = EntityNotFoundError("Machine", "m-999")
        resp = InfrastructureErrorResponse.from_exception(exc)
        assert resp.error_code == "ENTITY_NOT_FOUND"
        assert resp.message == "Resource not found"
        assert resp.category == ErrorCategory.ENTITY_NOT_FOUND
        assert resp.http_status == HTTPStatus.NOT_FOUND
        # entity_type is read via getattr(exception, "entity_type", "unknown")
        # EntityNotFoundError stores it only in details, not as a direct attr
        assert "entity_type" in resp.details

    def test_business_rule_violation_error(self) -> None:
        exc = BusinessRuleViolationError("quota exceeded", details={"rule": "max-machines"})
        resp = InfrastructureErrorResponse.from_exception(exc)
        assert resp.error_code == "BUSINESS_RULE_VIOLATION"
        assert resp.message == "Request could not be processed"
        assert resp.category == ErrorCategory.BUSINESS_RULE_VIOLATION
        assert resp.http_status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert resp.details.get("rule") == "max-machines"

    def test_configuration_error(self) -> None:
        exc = ConfigurationError("missing key")
        resp = InfrastructureErrorResponse.from_exception(exc)
        assert resp.error_code == "CONFIGURATION_ERROR"
        assert resp.message == "A configuration error occurred"
        assert resp.category == ErrorCategory.CONFIGURATION
        assert resp.http_status == HTTPStatus.INTERNAL_SERVER_ERROR

    def test_infrastructure_error(self) -> None:
        exc = InfrastructureError("db down")
        resp = InfrastructureErrorResponse.from_exception(exc)
        assert resp.error_code == "INFRASTRUCTURE_ERROR"
        assert resp.message == "An infrastructure error occurred"
        assert resp.category == ErrorCategory.DATABASE_ERROR
        assert resp.http_status == HTTPStatus.INTERNAL_SERVER_ERROR

    def test_unexpected_error(self) -> None:
        exc = RuntimeError("kaboom")
        resp = InfrastructureErrorResponse.from_exception(exc)
        assert resp.error_code == "UNEXPECTED_ERROR"
        assert resp.message == "An unexpected error occurred"
        assert resp.category == ErrorCategory.UNEXPECTED_ERROR
        assert resp.http_status == HTTPStatus.INTERNAL_SERVER_ERROR
        assert resp.details.get("exception_type") == "RuntimeError"

    def test_context_argument_not_forwarded_to_wire(self) -> None:
        """context is accepted but must not appear in response details."""
        exc = RuntimeError("internal host: db.internal")
        resp = InfrastructureErrorResponse.from_exception(exc, context="aws region us-east-1")
        # message must not contain internal context
        assert "db.internal" not in resp.message
        assert "us-east-1" not in str(resp.details)

    def test_unsafe_details_stripped_on_validation_error(self) -> None:
        exc = ValidationError("bad", details={"field": "x", "original_error": "leak"})
        resp = InfrastructureErrorResponse.from_exception(exc)
        assert "original_error" not in resp.details
        assert resp.details.get("field") == "x"


@pytest.mark.unit
class TestInfrastructureErrorResponseSerialisation:
    """Tests for to_api_response and to_dict."""

    def test_to_api_response_shape(self) -> None:
        resp = InfrastructureErrorResponse.from_domain_error(
            error_code="X", message="m", category=ErrorCategory.VALIDATION
        )
        data = resp.to_api_response()
        assert data["status"] == "error"
        assert "timestamp" in data
        assert data["error"]["code"] == "X"
        assert data["error"]["message"] == "m"
        assert data["error"]["category"] == ErrorCategory.VALIDATION

    def test_to_dict_shape(self) -> None:
        resp = InfrastructureErrorResponse.from_domain_error(
            error_code="Y", message="n", http_status=HTTPStatus.NOT_FOUND
        )
        data = resp.to_dict()
        assert data["status"] == HTTPStatus.NOT_FOUND
        assert data["error"]["code"] == "Y"

    def test_timestamp_is_iso_string(self) -> None:
        resp = InfrastructureErrorResponse.from_domain_error(error_code="Z", message="z")
        api = resp.to_api_response()
        # Must be parseable ISO string
        from datetime import datetime

        datetime.fromisoformat(api["timestamp"])


@pytest.mark.unit
class TestDetermineHttpStatus:
    """Tests for _determine_http_status coverage of all mapped categories."""

    def _status_for(self, category: str) -> int:
        return InfrastructureErrorResponse._determine_http_status(category)

    def test_validation_returns_400(self) -> None:
        assert self._status_for(ErrorCategory.VALIDATION) == HTTPStatus.BAD_REQUEST

    def test_entity_not_found_returns_404(self) -> None:
        assert self._status_for(ErrorCategory.ENTITY_NOT_FOUND) == HTTPStatus.NOT_FOUND

    def test_template_not_found_returns_404(self) -> None:
        assert self._status_for(ErrorCategory.TEMPLATE_NOT_FOUND) == HTTPStatus.NOT_FOUND

    def test_machine_not_found_returns_404(self) -> None:
        assert self._status_for(ErrorCategory.MACHINE_NOT_FOUND) == HTTPStatus.NOT_FOUND

    def test_request_not_found_returns_404(self) -> None:
        assert self._status_for(ErrorCategory.REQUEST_NOT_FOUND) == HTTPStatus.NOT_FOUND

    def test_business_rule_returns_422(self) -> None:
        assert (
            self._status_for(ErrorCategory.BUSINESS_RULE_VIOLATION)
            == HTTPStatus.UNPROCESSABLE_ENTITY
        )

    def test_duplicate_returns_409(self) -> None:
        assert self._status_for(ErrorCategory.DUPLICATE) == HTTPStatus.CONFLICT

    def test_invalid_state_returns_409(self) -> None:
        assert self._status_for(ErrorCategory.INVALID_STATE) == HTTPStatus.CONFLICT

    def test_operation_not_allowed_returns_403(self) -> None:
        assert self._status_for(ErrorCategory.OPERATION_NOT_ALLOWED) == HTTPStatus.FORBIDDEN

    def test_network_error_returns_502(self) -> None:
        assert self._status_for(ErrorCategory.NETWORK_ERROR) == HTTPStatus.BAD_GATEWAY

    def test_external_service_returns_502(self) -> None:
        assert self._status_for(ErrorCategory.EXTERNAL_SERVICE_ERROR) == HTTPStatus.BAD_GATEWAY

    def test_unknown_category_returns_500(self) -> None:
        assert self._status_for("some_unknown_category") == HTTPStatus.INTERNAL_SERVER_ERROR
