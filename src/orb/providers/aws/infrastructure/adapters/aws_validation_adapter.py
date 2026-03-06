"""AWS Validation Adapter - AWS-specific implementation of provider validation."""

from typing import Any

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_validation_port import BaseProviderValidationAdapter
from orb.providers.aws.configuration.validator import (
    AWSHandlerConfig,
    AWSProviderConfig,
)

# Static handler capability data — no runtime config needed.
_HANDLER_CONFIG = AWSHandlerConfig()


@injectable
class AWSValidationAdapter(BaseProviderValidationAdapter):
    """
    AWS implementation of provider validation operations.

    This adapter encapsulates all AWS-specific validation logic that requires
    access to AWS configuration. It maintains clean architecture by keeping
    configuration dependencies in the infrastructure layer.

    Features:
    - Provider API validation against AWS configuration
    - Fleet type compatibility validation
    - Default value resolution from AWS configuration hierarchy
    - AWS-specific template validation
    """

    def __init__(self, config: AWSProviderConfig, logger: LoggingPort) -> None:
        """
        Initialize AWS validation adapter.

        Args:
            config: AWS provider configuration
            logger: Logger for validation operations
        """
        self._config = config
        self._logger = logger

    def get_provider_type(self) -> str:
        """Get the provider type this adapter supports."""
        return "aws"

    def validate_provider_api(self, api: str) -> bool:
        """
        Validate if a provider API is supported by AWS.

        Args:
            api: The provider API identifier to validate

        Returns:
            True if the API is supported by AWS configuration
        """
        is_valid = api in _HANDLER_CONFIG.capabilities
        if not is_valid:
            self._logger.debug(
                "AWS API validation failed: %s not in %s",
                api,
                list(_HANDLER_CONFIG.capabilities.keys()),
            )
        return is_valid

    def get_supported_provider_apis(self) -> list[str]:
        """
        Get list of all supported AWS provider APIs.

        Returns:
            List of supported AWS provider API identifiers
        """
        return list(_HANDLER_CONFIG.capabilities.keys())

    def get_default_fleet_type_for_api(self, api: str) -> str:
        """
        Get the default fleet type for a specific AWS provider API.

        Args:
            api: The AWS provider API identifier

        Returns:
            Default fleet type for the API

        Raises:
            ValueError: If API is not supported
        """
        if not self.validate_provider_api(api):
            raise ValueError(f"Unsupported AWS provider API: {api}")

        try:
            # Get handler capabilities from configuration
            handler_capabilities = self._config.handlers.capabilities.get(api, {})  # type: ignore[attr-defined]

            if handler_capabilities and hasattr(handler_capabilities, "default_fleet_type"):
                return handler_capabilities.default_fleet_type

            # Fallback to hardcoded defaults based on API type
            if api == "EC2Fleet":
                return "instant"
            elif api == "SpotFleet":
                return "request"
            else:
                return "request"

        except Exception as e:
            self._logger.error("Error getting default fleet type for AWS API %s: %s", api, e)
            return "request"  # Safe fallback

    def get_valid_fleet_types_for_api(self, api: str) -> list[str]:
        """
        Get valid fleet types for a specific AWS provider API.

        Args:
            api: The AWS provider API identifier

        Returns:
            List of valid fleet types for the API

        Raises:
            ValueError: If API is not supported
        """
        if not self.validate_provider_api(api):
            raise ValueError(f"Unsupported AWS provider API: {api}")

        capabilities = _HANDLER_CONFIG.capabilities.get(api)
        if capabilities is None or capabilities.supported_fleet_types is None:
            return []
        return list(capabilities.supported_fleet_types)

    def validate_fleet_type_for_api(self, fleet_type: str, api: str) -> bool:
        """
        Validate if a fleet type is compatible with an AWS provider API.

        Args:
            fleet_type: The fleet type to validate
            api: The AWS provider API identifier

        Returns:
            True if the fleet type is compatible with the API
        """
        try:
            valid_types = self.get_valid_fleet_types_for_api(api)
            is_valid = fleet_type in valid_types

            if not is_valid:
                self._logger.debug(
                    "AWS fleet type validation failed: %s not valid for %s",
                    fleet_type,
                    api,
                )

            return is_valid

        except Exception as e:
            self._logger.error(
                "Error validating fleet type %s for AWS API %s: %s", fleet_type, api, e
            )
            return False

    def validate_template_configuration(self, template_config: dict[str, Any]) -> dict[str, Any]:
        """
        Validate a complete AWS template configuration.

        Args:
            template_config: Template configuration dictionary

        Returns:
            Validation result with 'valid', 'errors', and 'warnings' keys
        """
        errors: list[str] = []
        warnings: list[str] = []
        validated_fields: list[str] = []

        try:
            # Validate provider API
            provider_api = template_config.get("provider_api")
            if provider_api:
                validated_fields.append("provider_api")
                if not self.validate_provider_api(provider_api):
                    errors.append(f"Unsupported AWS provider API: {provider_api}")
                else:
                    # Validate fleet type compatibility if present
                    fleet_type = template_config.get("fleet_type")
                    if fleet_type:
                        validated_fields.append("fleet_type")
                        if not self.validate_fleet_type_for_api(fleet_type, provider_api):
                            errors.append(
                                f"Fleet type '{fleet_type}' is not compatible with AWS API '{provider_api}'"
                            )

            # Validate AWS-specific fields
            self._validate_aws_specific_fields(template_config, errors, warnings, validated_fields)

        except Exception as e:
            self._logger.error("Error during AWS template validation: %s", e)
            errors.append(f"Validation error: {e!s}")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "validated_fields": validated_fields,
        }

    def _validate_aws_specific_fields(
        self,
        template_config: dict[str, Any],
        errors: list[str],
        warnings: list[str],
        validated_fields: list[str],
    ) -> None:
        """
        Validate AWS-specific template fields.

        Args:
            template_config: Template configuration dictionary
            errors: List to append validation errors to
            warnings: List to append validation warnings to
            validated_fields: List to append validated field names to
        """
        # Validate AMI ID format (skip check for SSM parameter paths)
        image_id = template_config.get("image_id")
        if image_id:
            validated_fields.append("image_id")
            if not image_id.startswith("/") and not image_id.startswith("ami-"):
                errors.append(f"Invalid AWS AMI ID format: {image_id}")

        # Validate instance type format
        instance_type = template_config.get("instance_type")
        if instance_type:
            validated_fields.append("instance_type")
            if not self._is_valid_instance_type(instance_type):
                warnings.append(f"Uncommon AWS instance type: {instance_type}")

        # Validate subnet IDs
        subnet_ids = template_config.get("subnet_ids", [])
        if subnet_ids:
            validated_fields.append("subnet_ids")
            for subnet_id in subnet_ids:
                if not subnet_id.startswith("subnet-"):
                    errors.append(f"Invalid AWS subnet ID format: {subnet_id}")

        # Validate security group IDs
        security_group_ids = template_config.get("security_group_ids", [])
        if security_group_ids:
            validated_fields.append("security_group_ids")
            for sg_id in security_group_ids:
                if not sg_id.startswith("sg-"):
                    errors.append(f"Invalid AWS security group ID format: {sg_id}")

        # Validate spot configuration
        percent_on_demand = template_config.get("percent_on_demand")
        if percent_on_demand is not None:
            validated_fields.append("percent_on_demand")
            if not (0 <= percent_on_demand <= 100):
                errors.append("percent_on_demand must be between 0 and 100")

    def _is_valid_instance_type(self, instance_type: str) -> bool:
        """
        Check if instance type follows AWS naming convention.

        Args:
            instance_type: Instance type to validate

        Returns:
            True if instance type appears to be valid AWS format
        """
        # Basic AWS instance type validation (family.size)
        if "." not in instance_type:
            return False

        family, size = instance_type.split(".", 1)

        # Common AWS instance families
        common_families = [
            "t2",
            "t3",
            "t3a",
            "t4g",
            "m5",
            "m5a",
            "m5n",
            "m6i",
            "c5",
            "c5n",
            "c6i",
            "r5",
            "r5a",
            "r6i",
            "x1e",
            "z1d",
        ]

        return family in common_families
