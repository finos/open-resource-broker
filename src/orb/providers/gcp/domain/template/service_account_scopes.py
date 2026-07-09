"""Validation for GCP service-account OAuth scopes."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator


GCP_OAUTH_SCOPE_PREFIX = "https://www.googleapis.com/auth/"


def validate_gcp_service_account_scopes(scopes: list[str]) -> list[str]:
    """Validate OAuth scopes accepted for GCP service accounts."""
    if not scopes:
        raise ValueError("service_account_scopes must include at least one OAuth scope")

    validated_scopes: list[str] = []
    for scope in scopes:
        normalized_scope = scope.strip()
        if normalized_scope != scope or not normalized_scope:
            raise ValueError("service_account_scopes must contain non-empty scope URLs")
        if not normalized_scope.startswith(GCP_OAUTH_SCOPE_PREFIX):
            raise ValueError(
                "service_account_scopes must use https://www.googleapis.com/auth/ URLs"
            )
        if normalized_scope == GCP_OAUTH_SCOPE_PREFIX:
            raise ValueError("service_account_scopes must include a concrete OAuth scope name")
        validated_scopes.append(normalized_scope)

    return validated_scopes


GCPServiceAccountScopes = Annotated[
    list[str],
    AfterValidator(validate_gcp_service_account_scopes),
]
