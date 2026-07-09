"""Adapter bridging GCP template validation into the ORB validation port."""

from __future__ import annotations

from typing import Any

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_validation_port import BaseProviderValidationAdapter
from orb.providers.gcp.capabilities import get_supported_api_capabilities, get_supported_apis
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.configuration.validator import validate_gcp_template


@injectable
class GCPValidationAdapter(BaseProviderValidationAdapter):
    """GCP validation adapter."""

    def __init__(self, config: GCPProviderConfig, logger: LoggingPort) -> None:
        self._config = config
        self._logger = logger

    def get_provider_type(self) -> str:
        """Return the provider type handled by this adapter."""
        return "gcp"

    def validate_provider_api(self, api: str) -> bool:
        """Report whether the requested GCP provider API is supported."""
        try:
            return api in set(get_supported_apis())
        except Exception as exc:
            self._logger.error("Error validating GCP provider API %s: %s", api, exc)
            return api in {"MIG", "SingleVM"}

    def get_supported_provider_apis(self) -> list[str]:
        """List supported GCP provider APIs."""
        try:
            return sorted(get_supported_apis())
        except Exception as exc:
            self._logger.error("Error getting supported GCP APIs: %s", exc)
            return ["MIG", "SingleVM"]

    @staticmethod
    def get_api_capabilities(api: str) -> dict[str, Any]:
        """Return capability metadata for one supported API."""
        capabilities = get_supported_api_capabilities().get(api)
        if capabilities is None:
            raise ValueError(f"Unsupported GCP provider API: {api}")
        return capabilities

    def validate_template_configuration(self, template_config: dict[str, Any]) -> dict[str, Any]:
        """Merge generic validation results with GCP template-specific validation."""
        base_result = super().validate_template_configuration(template_config)
        gcp_result = validate_gcp_template(template_config)

        errors = list(dict.fromkeys([*base_result.get("errors", []), *gcp_result.get("errors", [])]))
        warnings = list(
            dict.fromkeys([*base_result.get("warnings", []), *gcp_result.get("warnings", [])])
        )
        validated_fields = list(
            dict.fromkeys(
                [*base_result.get("validated_fields", []), *gcp_result.get("validated_fields", [])]
            )
        )
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "validated_fields": validated_fields,
        }
