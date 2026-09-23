"""Azure provider error-code payloads."""

from __future__ import annotations

from typing import TypedDict


class ProviderErrorEntry(TypedDict, total=False):
    """Normalized Azure infrastructure error payload."""

    error_code: str
    error_message: str
    instance_id: str
    resource_id: str
    node_array: str
    cc_state: str
    lifecycle: str | None
    launch_template_id: str | None
    launch_template_version: str | None
    subnet_id: str | None
    instance_type: str | None
    instance_requirements: object
    status_code: str | None
    status_level: str | None


def collect_provider_error_codes(errors: list[ProviderErrorEntry]) -> list[str]:
    """Return unique canonical error codes from normalized Azure errors."""
    error_codes: list[str] = []
    for error in errors:
        error_code = error.get("error_code")
        if error_code and error_code not in error_codes:
            error_codes.append(str(error_code))
    return error_codes
