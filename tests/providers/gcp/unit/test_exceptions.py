"""GCP errors retain the domain exception's error-code contract."""

from google.api_core import exceptions as google_exceptions

from orb.providers.gcp.exceptions import GCPValidationError, translate_gcp_exception


def test_translate_gcp_exception_maps_invalid_argument_to_validation_error() -> None:
    exc = google_exceptions.InvalidArgument("Invalid value for field 'resource.name'")

    translated = translate_gcp_exception(exc, operation="create_instance")

    assert isinstance(translated, GCPValidationError)
    assert translated.details["operation"] == "create_instance"


def test_gcp_error_uses_default_domain_error_code() -> None:
    error = GCPValidationError("Invalid machine")

    assert error.error_code == "GCPValidationError"
    assert error.to_dict()["error_code"] == "GCPValidationError"


def test_gcp_error_preserves_explicit_error_code() -> None:
    error = GCPValidationError("Invalid machine", details={"machine": "vm-1"}, error_code="BAD_VM")

    assert error.error_code == "BAD_VM"
    assert error.to_dict()["error_code"] == "BAD_VM"
    assert error.to_dict()["details"] == {"machine": "vm-1"}
