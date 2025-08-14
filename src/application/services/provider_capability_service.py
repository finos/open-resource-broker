"""Provider Capability Service - Template validation against provider capabilities.

This service validates template requirements against provider capabilities,
following Clean Architecture and Single Responsibility Principle.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from src.domain.base.ports import LoggingPort
from src.domain.template.aggregate import Template
from src.infrastructure.registry.provider_registry import ProviderRegistry
from src.providers.base.strategy.provider_strategy import (
    ProviderCapabilities,
    ProviderOperationType,
)


@dataclass
class ValidationResult:
    """Result of template capability validation."""

    is_valid: bool
    provider_instance: str
    errors: List[str]
    warnings: List[str]
    supported_features: List[str]
    unsupported_features: List[str]

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
        if self.supported_features is None:
            self.supported_features = []
        if self.unsupported_features is None:
            self.unsupported_features = []


class ValidationLevel(str, Enum):
    """Validation strictness levels."""

    STRICT = "strict"  # All requirements must be met
    LENIENT = "lenient"  # Warnings for unsupported features
    BASIC = "basic"  # Only check critical requirements


class ProviderCapabilityService:
    """
    Service for validating provider capabilities against template requirements.

    This service implements capability validation following Clean Architecture:
    - Domain logic for validation rules
    - Infrastructure abstraction for provider capabilities
    - Clear separation of validation concerns

    Responsibilities:
    - Validate template requirements against provider capabilities
    - Check API support and compatibility
    - Validate pricing model support (spot/on-demand)
    - Check fleet type compatibility
    - Provide detailed validation results with actionable feedback
    """

    def __init__(self, logger: LoggingPort, provider_registry: Optional[ProviderRegistry] = None):
        """
        Initialize provider capability service.

        Args:
            logger: Logger for validation results and debugging
            provider_registry: Optional provider registry for capability queries
        """
        self._logger = logger
        self._provider_registry = provider_registry

    def validate_template_requirements(
        self,
        template: Template,
        provider_instance: str,
        validation_level: ValidationLevel = ValidationLevel.STRICT,
    ) -> ValidationResult:
        """
        Validate template requirements against provider capabilities.

        This method performs comprehensive validation of template requirements
        against the specified provider's capabilities.

        Args:
            template: Template to validate
            provider_instance: Provider instance name to validate against
            validation_level: Strictness of validation

        Returns:
            ValidationResult with detailed validation information
        """
        self._logger.info(
            f"Validating template { template.template_id} against provider {provider_instance}"
        )

        result = ValidationResult(
            is_valid=True,
            provider_instance=provider_instance,
            errors=[],
            warnings=[],
            supported_features=[],
            unsupported_features=[],
        )

        try:
            # Get provider capabilities
            capabilities = self._get_provider_capabilities(provider_instance)
            if not capabilities:
                result.is_valid = False
                result.errors.append(
                    f"Cannot retrieve capabilities for provider {provider_instance}"
                )
                return result

            # Validate core requirements
            self._validate_api_support(template, capabilities, result)
            self._validate_pricing_model(template, capabilities, result)
            self._validate_fleet_type_support(template, capabilities, result)
            self._validate_instance_limits(template, capabilities, result)

            # Apply validation level rules
            if validation_level == ValidationLevel.STRICT and result.warnings:
                # In strict mode, warnings become errors
                result.errors.extend(result.warnings)
                result.warnings = []
                result.is_valid = False
            elif validation_level == ValidationLevel.BASIC:
                # In basic mode, only check critical errors
                result.warnings = []

            # Set final validation status
            result.is_valid = len(result.errors) == 0

            self._logger.info(
                f"Validation result for { template.template_id}: { 'VALID' if result.is_valid else 'INVALID'}"
            )

        except Exception as e:
            self._logger.error(f"Validation failed with exception: {str(e)}")
            result.is_valid = False
            result.errors.append(f"Validation error: {str(e)}")

        return result

    def _get_provider_capabilities(self, provider_instance: str) -> Optional[ProviderCapabilities]:
        """Get capabilities for specified provider instance."""
        if not self._provider_registry:
            # Fallback to hardcoded capabilities for testing
            return self._get_default_capabilities(provider_instance)

        try:
            # Get provider strategy from registry
            strategy = self._provider_registry.create_strategy_from_instance(provider_instance, {})
            return strategy.get_capabilities()
        except Exception as e:
            self._logger.warning(f"Failed to get capabilities for {provider_instance}: {str(e)}")
            return None

    def _get_default_capabilities(self, provider_instance: str) -> ProviderCapabilities:
        """Get default capabilities based on provider instance name."""
        # Extract provider type from instance name
        provider_type = (
            provider_instance.split("-")[0] if "-" in provider_instance else provider_instance
        )

        if provider_type == "aws":
            return ProviderCapabilities(
                provider_type="aws",
                supported_operations=[
                    ProviderOperationType.CREATE_INSTANCES,
                    ProviderOperationType.TERMINATE_INSTANCES,
                    ProviderOperationType.GET_INSTANCE_STATUS,
                ],
                features={
                    "supported_apis": ["EC2Fleet", "SpotFleet", "RunInstances", "ASG"],
                    "api_capabilities": {
                        "EC2Fleet": {
                            "supported_fleet_types": ["instant", "request", "maintain"],
                            "supports_spot": True,
                            "supports_on_demand": True,
                            "max_instances": 1000,
                        },
                        "SpotFleet": {
                            "supported_fleet_types": ["request", "maintain"],
                            "supports_spot": True,
                            "supports_on_demand": False,
                            "max_instances": 1000,
                        },
                        "RunInstances": {
                            "supported_fleet_types": [],
                            "supports_spot": False,
                            "supports_on_demand": True,
                            "max_instances": 100,
                        },
                        "ASG": {
                            "supported_fleet_types": [],
                            "supports_spot": True,
                            "supports_on_demand": True,
                            "max_instances": 1000,
                        },
                    },
                    "spot_instances": True,
                    "fleet_management": True,
                    "auto_scaling": True,
                },
            )
        else:
            # Default capabilities for unknown providers
            return ProviderCapabilities(
                provider_type=provider_type,
                supported_operations=[ProviderOperationType.CREATE_INSTANCES],
                features={},
            )

    def _validate_api_support(
        self,
        template: Template,
        capabilities: ProviderCapabilities,
        result: ValidationResult,
    ):
        """Validate that provider supports the required API."""
        if not template.provider_api:
            result.warnings.append("No provider API specified in template")
            return

        supported_apis = capabilities.get_feature("supported_apis", [])
        if template.provider_api not in supported_apis:
            result.errors.append(
                f"Provider does not support API '{ template.provider_api}'. Supported APIs: {supported_apis}"
            )
        else:
            result.supported_features.append(f"API: {template.provider_api}")

    def _validate_pricing_model(
        self,
        template: Template,
        capabilities: ProviderCapabilities,
        result: ValidationResult,
    ):
        """Validate pricing model support (spot/on-demand)."""
        if not template.provider_api:
            return

        api_capabilities = capabilities.get_feature("api_capabilities", {})
        api_caps = api_capabilities.get(template.provider_api, {})

        price_type = getattr(template, "price_type", "ondemand")

        if price_type == "spot":
            if not api_caps.get("supports_spot", False):
                result.errors.append(
                    f"API '{template.provider_api}' does not support spot instances"
                )
            else:
                result.supported_features.append("Pricing: Spot instances")
        elif price_type == "ondemand":
            if not api_caps.get("supports_on_demand", True):
                result.errors.append(
                    f"API '{ template.provider_api}' does not support on-demand instances"
                )
            else:
                result.supported_features.append("Pricing: On-demand instances")

    def _validate_fleet_type_support(
        self,
        template: Template,
        capabilities: ProviderCapabilities,
        result: ValidationResult,
    ):
        """Validate fleet type support."""
        if not template.provider_api:
            return

        # Check if template has fleet_type (might be in metadata or other fields)
        fleet_type = getattr(template, "fleet_type", None)
        if not fleet_type:
            # Try to get from metadata
            fleet_type = template.metadata.get("fleet_type") if template.metadata else None

        if not fleet_type:
            return  # No fleet type specified

        api_capabilities = capabilities.get_feature("api_capabilities", {})
        api_caps = api_capabilities.get(template.provider_api, {})
        supported_fleet_types = api_caps.get("supported_fleet_types", [])

        if supported_fleet_types and fleet_type not in supported_fleet_types:
            result.errors.append(
                f"API '{ template.provider_api}' does not support fleet type '{fleet_type}'. Supported types: {supported_fleet_types}"
            )
        elif supported_fleet_types:
            result.supported_features.append(f"Fleet type: {fleet_type}")

    def _validate_instance_limits(
        self,
        template: Template,
        capabilities: ProviderCapabilities,
        result: ValidationResult,
    ):
        """Validate instance count limits."""
        if not template.provider_api:
            return

        api_capabilities = capabilities.get_feature("api_capabilities", {})
        api_caps = api_capabilities.get(template.provider_api, {})
        max_instances = api_caps.get("max_instances", float("inf"))

        if template.max_instances > max_instances:
            result.errors.append(
                f"Requested { template.max_instances} instances exceeds API limit of {max_instances}"
            )
        else:
            result.supported_features.append(
                f"Instance count: {template.max_instances} (within limit)"
            )

    def get_provider_api_capabilities(self, provider_instance: str, api: str) -> Dict[str, Any]:
        """Get detailed capabilities for specific provider API."""
        capabilities = self._get_provider_capabilities(provider_instance)
        if not capabilities:
            return {}

        api_capabilities = capabilities.get_feature("api_capabilities", {})
        return api_capabilities.get(api, {})

    def list_supported_apis(self, provider_instance: str) -> List[str]:
        """List all APIs supported by provider instance."""
        capabilities = self._get_provider_capabilities(provider_instance)
        if not capabilities:
            return []

        return capabilities.get_feature("supported_apis", [])

    def check_api_compatibility(
        self, template: Template, provider_instances: List[str]
    ) -> Dict[str, ValidationResult]:
        """Check template compatibility across multiple provider instances."""
        results = {}

        for provider_instance in provider_instances:
            results[provider_instance] = self.validate_template_requirements(
                template, provider_instance, ValidationLevel.LENIENT
            )

        return results
