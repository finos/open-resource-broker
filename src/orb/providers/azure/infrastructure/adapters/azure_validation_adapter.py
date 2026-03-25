from typing import Any

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_validation_port import BaseProviderValidationAdapter
from orb.providers.azure import AzureProviderConfig
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
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

    Note: this adapter is not yet registered in ``azure/registration.py``.
    AWS's equivalent is wired via ``validator_factory`` in the provider
    registry and used by ``ProviderValidationService``.  Azure should
    follow the same pattern.
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
            supported_apis = {provider_api.value for provider_api in AzureProviderApi}
            is_valid = api in supported_apis

            if not is_valid:
                self._logger.debug("azure API validation failed: %s not in %s", api, supported_apis)

            return is_valid

        except Exception as e:
            self._logger.error("Error validating azure provider API %s: %s", api, e)
            # Fall back to the domain enum so validation still works without config-manager wiring.
            return api in {provider_api.value for provider_api in AzureProviderApi}

    def get_supported_provider_apis(self) -> list[str]:
        """
        Get list of all supported azure provider APIs.

        Returns:
            List of supported azure provider API identifiers
        """
        try:
            return sorted(provider_api.value for provider_api in AzureProviderApi)
        except Exception as e:
            self._logger.error("Error getting supported azure APIs: %s", e)
            # Fallback to hardcoded list for safety
            return ["VMSS", "CycleCloud", "SingleVM", "VMSSUniform"]

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
