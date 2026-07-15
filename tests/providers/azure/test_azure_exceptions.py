"""Tests for Azure's provider-neutral exception contract."""

import pytest

from orb.providers.azure.exceptions import (
    AuthenticationError,
    AzureConfigurationError,
    AzureError,
    AzureInfrastructureError,
    AzureValidationError,
    NetworkError,
    QuotaExceededError,
    RateLimitError,
)
from orb.providers.base.exceptions import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderError,
    ProviderPermanentError,
    ProviderQuotaError,
    ProviderTransientError,
)


@pytest.mark.parametrize(
    ("azure_error_type", "provider_error_type", "is_retryable"),
    [
        (AzureValidationError, ProviderPermanentError, False),
        (AuthenticationError, ProviderAuthError, False),
        (AzureConfigurationError, ProviderConfigError, False),
        (QuotaExceededError, ProviderQuotaError, True),
        (RateLimitError, ProviderQuotaError, True),
        (NetworkError, ProviderTransientError, True),
        (AzureInfrastructureError, ProviderTransientError, True),
    ],
)
def test_azure_errors_declare_provider_failure_semantics(
    azure_error_type: type[AzureError],
    provider_error_type: type[ProviderError],
    is_retryable: bool,
) -> None:
    error = azure_error_type("operation failed")

    assert isinstance(error, provider_error_type)
    assert error.provider_type == "azure"
    assert error.is_retryable is is_retryable


def test_safe_serialization_omits_underlying_exception_and_preserves_error_code() -> None:
    error = NetworkError(
        "Azure API unavailable",
        error_code="ServiceUnavailable",
        underlying_exception=RuntimeError("secret-bearing SDK response"),
    )

    assert error.to_dict()["underlying_exception"] == "RuntimeError('secret-bearing SDK response')"
    assert error.safe_to_dict() == {
        "error_type": "NetworkError",
        "message": "Azure API unavailable",
        "provider_type": "azure",
        "is_retryable": True,
        "error_code": "ServiceUnavailable",
    }
