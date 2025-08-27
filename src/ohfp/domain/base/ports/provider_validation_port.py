"""Provider Validation Port - Interface for provider-specific validation operations."""

from abc import ABC, abstractmethod
from typing import Any, Protocol


class ProviderValidationPort(Protocol):
    """Port for provider-specific validation operations.

    This port defines the interface for provider-specific validation logic
    that needs to access provider configuration. It maintains clean architecture
    by keeping domain objects free from infrastructure dependencies.

    Implementations should provide:
    - Provider API validation against configuration
    - Default value resolution based on provider configuration
    - Field compatibility validation
    - Provider-specific constraint validation
    """

    def validate_provider_api(self, api: str) -> bool:
        """
        Validate if a provider API is supported by this provider.

        Args:
            api: The provider API identifier to validate

        Returns:
            True if the API is supported, False otherwise
        """
        ...

    def get_supported_provider_apis(self) -> list[str]:
        """
        Get list of all supported provider APIs.

        Returns:
            List of supported provider API identifiers
        """
        ...

    def get_default_fleet_type_for_api(self, api: str) -> str:
        """
        Get the default fleet type for a specific provider API.

        Args:
            api: The provider API identifier

        Returns:
            Default fleet type for the API

        Raises:
            ValueError: If API is not supported
        """
        ...

    def get_valid_fleet_types_for_api(self, api: str) -> list[str]:
        """
        Get valid fleet types for a specific provider API.

        Args:
            api: The provider API identifier

        Returns:
            List of valid fleet types for the API

        Raises:
            ValueError: If API is not supported
        """
        ...

    def validate_fleet_type_for_api(self, fleet_type: str, api: str) -> bool:
        """
        Validate if a fleet type is compatible with a provider API.

        Args:
            fleet_type: The fleet type to validate
            api: The provider API identifier

        Returns:
            True if the fleet type is compatible with the API
        """
        ...

    def get_provider_type(self) -> str:
        """
        Get the provider type this validation port supports.

        Returns:
            Provider type identifier (e.g., 'aws', 'provider1', 'provider2')
        """
        ...

    def validate_template_configuration(self, template_config: dict[str, Any]) -> dict[str, Any]:
        """
        Validate a complete template configuration for this provider.

        Args:
            template_config: Template configuration dictionary

        Returns:
            Validation result with 'valid', 'errors', and 'warnings' keys
        """
        ...


class BaseProviderValidationAdapter(ABC):
    """Abstract base class for provider validation adapters.

    This class provides common functionality for provider validation
    implementations while enforcing the interface contract.
    """

    @abstractmethod
    def get_provider_type(self) -> str:
        """Get the provider type this adapter supports."""

    @abstractmethod
    def validate_provider_api(self, api: str) -> bool:
        """Validate provider API support."""

    @abstractmethod
    def get_supported_provider_apis(self) -> list[str]:
        """Get supported provider APIs."""

    def validate_template_configuration(self, template_config: dict[str, Any]) -> dict[str, Any]:
        """
        Validate template configuration with defaults.

        Subclasses can override this method to provide provider-specific validation.

        Args:
            template_config: Template configuration dictionary

        Returns:
            Validation result dictionary
        """
        errors = []
        warnings = []

        # Basic validation - check for provider API
        provider_api = template_config.get("provider_api")
        if provider_api and not self.validate_provider_api(provider_api):
            errors.append(f"Unsupported provider API: {provider_api}")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "validated_fields": list(template_config.keys()),
        }
