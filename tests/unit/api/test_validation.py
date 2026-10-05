"""Unit tests for ``orb.api.validation`` request validation utilities."""

import pytest
from pydantic import BaseModel

from orb.api.validation import (
    RequestValidator,
    ValidationException,
    create_error_response,
    validate_request_body,
)
from orb.domain.base.exceptions import InfrastructureError


class _Sample(BaseModel):
    """Minimal Pydantic model used to exercise validation behaviour."""

    name: str
    age: int


@pytest.mark.unit
@pytest.mark.api
class TestValidationException:
    """Behaviour of the ``ValidationException`` container."""

    def test_stores_message_and_errors(self):
        errors = [{"loc": ["body", "name"], "msg": "field required", "type": "missing"}]
        exc = ValidationException("bad request", errors)

        assert exc.message == "bad request"
        assert exc.errors == errors
        assert str(exc) == "bad request"

    def test_defaults_errors_to_empty_list_when_omitted(self):
        exc = ValidationException("bad request")

        assert exc.errors == []


@pytest.mark.unit
@pytest.mark.api
class TestValidateRequestBody:
    """Behaviour of ``validate_request_body`` for dict and string payloads."""

    def test_validates_dict_payload(self):
        result = validate_request_body(_Sample, {"name": "alice", "age": 30})

        assert isinstance(result, _Sample)
        assert result.name == "alice"
        assert result.age == 30

    def test_validates_json_string_payload(self):
        result = validate_request_body(_Sample, '{"name": "bob", "age": 42}')

        assert result.name == "bob"
        assert result.age == 42

    def test_invalid_json_string_raises_infrastructure_error(self):
        # The @handle_interface_exceptions decorator wraps the raised
        # ValidationException in an InfrastructureError before it escapes
        # the function boundary.
        with pytest.raises(InfrastructureError, match="Invalid JSON in request body"):
            validate_request_body(_Sample, "{not valid json")

    def test_model_validation_failure_raises_infrastructure_error(self):
        with pytest.raises(InfrastructureError, match="Validation error"):
            validate_request_body(_Sample, {"name": "alice"})

    def test_model_validation_failure_wraps_type_mismatch(self):
        with pytest.raises(InfrastructureError, match="Validation error"):
            validate_request_body(_Sample, {"name": "alice", "age": "not-an-int"})


@pytest.mark.unit
@pytest.mark.api
class TestCreateErrorResponse:
    """Behaviour of ``create_error_response``."""

    def test_without_errors_omits_errors_key(self):
        response = create_error_response("something went wrong")

        assert response == {"status": "error", "message": "something went wrong"}
        assert "errors" not in response

    def test_with_errors_includes_errors_key(self):
        errors = [{"loc": ["age"], "msg": "field required", "type": "missing"}]
        response = create_error_response("validation failed", errors)

        assert response["status"] == "error"
        assert response["message"] == "validation failed"
        assert response["errors"] == errors

    def test_with_empty_errors_list_omits_errors_key(self):
        response = create_error_response("validation failed", [])

        assert "errors" not in response


@pytest.mark.unit
@pytest.mark.api
class TestRequestValidator:
    """Behaviour of the ``RequestValidator`` static-method facade."""

    def test_validate_delegates_to_validate_request_body(self):
        result = RequestValidator.validate(_Sample, {"name": "carol", "age": 7})

        assert isinstance(result, _Sample)
        assert result.name == "carol"

    def test_validate_raises_infrastructure_error_on_bad_payload(self):
        with pytest.raises(InfrastructureError):
            RequestValidator.validate(_Sample, {"name": "carol"})

    def test_handle_validation_error_builds_error_response(self):
        errors = [{"loc": ["age"], "msg": "field required", "type": "missing"}]
        exc = ValidationException("validation failed", errors)

        response = RequestValidator.handle_validation_error(exc)

        assert response == {
            "status": "error",
            "message": "validation failed",
            "errors": errors,
        }
