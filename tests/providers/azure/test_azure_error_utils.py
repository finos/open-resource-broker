"""Focused tests for Azure error normalization helpers."""

from types import SimpleNamespace

from orb.providers.azure.infrastructure.error_utils import (
    canonical_azure_error_code,
    extract_azure_error_details,
)


def test_extract_azure_error_details_reads_common_exception_shapes():
    exc = SimpleNamespace(
        error_code="AllocationFailed",
        status_code=409,
        message="allocation failed in zone 1",
        error=None,
        response=None,
    )

    assert extract_azure_error_details(exc) == {
        "raw_error_code": "AllocationFailed",
        "status_code": 409,
        "message": "allocation failed in zone 1",
        "details": [],
    }


def test_extract_azure_error_details_falls_back_to_nested_error_and_response():
    exc = SimpleNamespace(
        error=SimpleNamespace(code="QuotaExceeded", message="quota exceeded"),
        response=SimpleNamespace(status_code=429),
    )

    assert extract_azure_error_details(exc) == {
        "raw_error_code": "QuotaExceeded",
        "status_code": 429,
        "message": "quota exceeded",
        "details": [],
    }


def test_extract_azure_error_details_preserves_nested_arm_details():
    exc = SimpleNamespace(
        error=SimpleNamespace(
            code="InvalidTemplateDeployment",
            message="Deployment validation failed",
            details=[
                {
                    "code": "InvalidParameter",
                    "message": "The supplied VM size is not available in this location.",
                }
            ],
        ),
        response=SimpleNamespace(status_code=400),
    )

    assert extract_azure_error_details(exc) == {
        "raw_error_code": "InvalidTemplateDeployment",
        "status_code": 400,
        "message": "Deployment validation failed",
        "details": [
            {
                "code": "InvalidParameter",
                "message": "The supplied VM size is not available in this location.",
            }
        ],
    }


def test_extract_azure_error_details_reads_wrapped_launch_error_details():
    exc = SimpleNamespace(
        error_code="InvalidRequest",
        message="Deployment validation failed",
        details={
            "raw_error_code": "InvalidTemplateDeployment",
            "status_code": 400,
            "details": [
                {
                    "code": "InvalidSubnet",
                    "message": "The subnet resource ID is invalid.",
                }
            ],
        },
    )

    assert extract_azure_error_details(exc) == {
        "raw_error_code": "InvalidRequest",
        "status_code": 400,
        "message": "Deployment validation failed",
        "details": [
            {
                "code": "InvalidSubnet",
                "message": "The subnet resource ID is invalid.",
            }
        ],
    }


def test_canonical_azure_error_code_prefers_raw_error_code():
    exc = SimpleNamespace(error_code="SkuNotAvailable", status_code=409, message="ignored")

    assert canonical_azure_error_code(exc) == "SkuNotAvailable"


def test_canonical_azure_error_code_maps_status_codes_and_messages():
    assert (
        canonical_azure_error_code(SimpleNamespace(status_code=429, error=None, response=None))
        == "TooManyRequests"
    )
    assert canonical_azure_error_code(Exception("quota exceeded for region")) == "QuotaExceeded"
    assert (
        canonical_azure_error_code(Exception("insufficient capacity to allocate"))
        == "AllocationFailed"
    )
    assert (
        canonical_azure_error_code(Exception("validation failed for parameter")) == "InvalidRequest"
    )
