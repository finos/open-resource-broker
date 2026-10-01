"""GCP errors retain the domain exception's error-code contract."""

from orb.providers.gcp.exceptions import GCPValidationError


def test_gcp_error_uses_default_domain_error_code() -> None:
    error = GCPValidationError("Invalid machine")

    assert error.error_code == "GCPValidationError"
    assert error.to_dict()["error_code"] == "GCPValidationError"


def test_gcp_error_preserves_explicit_error_code() -> None:
    error = GCPValidationError("Invalid machine", details={"machine": "vm-1"}, error_code="BAD_VM")

    assert error.error_code == "BAD_VM"
    assert error.to_dict()["error_code"] == "BAD_VM"
    assert error.to_dict()["details"] == {"machine": "vm-1"}
