from typing import Any

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_validation_port import BaseProviderValidationAdapter
from orb.providers.azure import AzureProviderConfig
from orb.providers.azure.capabilities import get_supported_api_capabilities, get_supported_apis
from orb.providers.azure.configuration.validator import validate_azure_template


@injectable
class AzureValidationAdapter(BaseProviderValidationAdapter):
    """
    azure implementation of the ProviderValidationAdapter interface.

    Encapsulates the Azure-specific validation logic that
    requires access to Azure configuration.

    Features:
    - Validates Azure provider API endpoints.
    - Retrieves supported Azure provider APIs.
    - Identifies the provider type as Azure.

    This adapter is wired via ``validator_factory`` in ``azure/registration.py``
    so validation can use Azure-native capability metadata without
    constructing the full provider strategy.
    """
    def __init__(self, config: AzureProviderConfig, logger: LoggingPort) -> None:
        """
        Initialize Azure validation adapter.

        Args:
            config: Azure provider configuration
            logger: Logger for validation operations
        """
        self._config = config
        self._logger = logger

    def get_provider_type(self) -> str:
        """Get the provider type this adapter supports."""
        return "azure"

    def validate_provider_api(self, api: str) -> bool:
        """
        Validate if a provider API is supported by azure.

        Args:
            api: The provider API identifier to validate

        Returns:
            True if the API is supported by azure configuration
        """
        try:
            supported_apis = set(get_supported_apis())
            is_valid = api in supported_apis

            if not is_valid:
                self._logger.debug("azure API validation failed: %s not in %s", api, supported_apis)

            return is_valid

        except Exception as e:
            self._logger.error("Error validating azure provider API %s: %s", api, e)
            # Fall back to canonical API names so validation still works without helper wiring.
            return api in {"VMSS", "CycleCloud", "SingleVM", "VMSSUniform"}

    def get_supported_provider_apis(self) -> list[str]:
        """
        Get list of all supported azure provider APIs.

        Returns:
            List of supported azure provider API identifiers
        """
        try:
            return sorted(get_supported_apis())
        except Exception as e:
            self._logger.error("Error getting supported azure APIs: %s", e)
            # Fallback to hardcoded list for safety
            return ["VMSS", "CycleCloud", "SingleVM", "VMSSUniform"]

    @staticmethod
    def get_api_capabilities(api: str) -> dict[str, Any]:
        """Get capability metadata for a specific Azure provider API."""
        capabilities = get_supported_api_capabilities().get(api)
        if capabilities is None:
            raise ValueError(f"Unsupported Azure provider API: {api}")
        return capabilities

    def validate_template_configuration(self, template_config: dict[str, Any]) -> dict[str, Any]:
        """Validate a complete Azure template configuration."""
        base_result = super().validate_template_configuration(template_config)
        azure_result = validate_azure_template(template_config)

        errors: list[str] = []
        warnings: list[str] = []
        validated_fields: list[str] = []

        for item in base_result.get("errors", []):
            if item not in errors:
                errors.append(item)
        for item in azure_result.get("errors", []):
            if item not in errors:
                errors.append(item)

        for item in base_result.get("warnings", []):
            if item not in warnings:
                warnings.append(item)
        for item in azure_result.get("warnings", []):
            if item not in warnings:
                warnings.append(item)

        for item in base_result.get("validated_fields", []):
            if item not in validated_fields:
                validated_fields.append(item)
        for item in azure_result.get("validated_fields", []):
            if item not in validated_fields:
                validated_fields.append(item)

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "validated_fields": validated_fields,
        }
