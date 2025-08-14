"""AWS Validation Adapter - AWS-specific implementation of provider validation."""

from typing import Any, Dict, List

from src.domain.base.dependency_injection import injectable
from src.domain.base.ports.logging_port import LoggingPort
from src.domain.base.ports.provider_validation_port import BaseProviderValidationAdapter
from src.providers.aws.configuration.validator import (
    AWSProviderConfig,
    get_aws_config_manager,
)


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

    def __init__(self, config: AWSProviderConfig, logger: LoggingPort):
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
        try:
            # Get supported APIs from configuration
            from src.config.manager import get_config_manager

            config_manager = get_config_manager()
            raw_config = config_manager.get_raw_config()

            # Navigate to AWS handlers in configuration
            aws_handlers = (
                raw_config.get("provider", {})
                .get("provider_defaults", {})
                .get("aws", {})
                .get("handlers", {})
            )

            supported_apis = list(aws_handlers.keys())
            is_valid = api in supported_apis

            if not is_valid:
                self._logger.debug(
                    f"AWS API validation failed: {api} not in {supported_apis}")

            return is_valid

        except Exception as e:
            self._logger.error(f"Error validating AWS provider API {api}: {e}")
            # Fallback to hardcoded list for safety
            return api in ["EC2Fleet", "SpotFleet", "ASG", "RunInstances"]

    def get_supported_provider_apis(self) -> List[str]:
        """
        Get list of all supported AWS provider APIs.

        Returns:
            List of supported AWS provider API identifiers
        """
        try:
            # Get supported APIs from configuration
            from src.config.manager import get_config_manager

            config_manager = get_config_manager()
            raw_config = config_manager.get_raw_config()

            # Navigate to AWS handlers in configuration
            aws_handlers = (
                raw_config.get("provider", {})
                .get("provider_defaults", {})
                .get("aws", {})
                .get("handlers", {})
            )

            return list(aws_handlers.keys())
        except Exception as e:
            self._logger.error(f"Error getting supported AWS APIs: {e}")
            # Fallback to hardcoded list for safety
            return ["EC2Fleet", "SpotFleet", "ASG", "RunInstances"]

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
            handler_capabilities = self._config.handlers.capabilities.get(api, {})

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
            self._logger.error(
                f"Error getting default fleet type for AWS API {api}: {e}")
            return "request"  # Safe fallback

    def get_valid_fleet_types_for_api(self, api: str) -> List[str]:
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

        try:
            # Get fleet types from configuration
            from src.config.manager import get_config_manager

            config_manager = get_config_manager()
            raw_config = config_manager.get_raw_config()

            # Navigate to specific handler configuration
            handler_config = (
                raw_config.get("provider", {})
                .get("provider_defaults", {})
                .get("aws", {})
                .get("handlers", {})
                .get(api, {})
            )

            # Get supported fleet types from configuration
            supported_fleet_types = handler_config.get("supported_fleet_types", [])

            if supported_fleet_types:
                return supported_fleet_types

            # Fallback to hardcoded values if not in config
            if api == "EC2Fleet":
                return ["instant", "request", "maintain"]
            elif api == "SpotFleet":
                return ["request", "maintain"]
            elif api == "ASG":
                return []  # ASG doesn't use fleet types
            elif api == "RunInstances":
                return []  # RunInstances doesn't use fleet types
            else:
                return ["request"]

        except Exception as e:
            self._logger.error(
                f"Error getting valid fleet types for AWS API {api}: {e}")
            return ["request"]  # Safe fallback

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
                    f"AWS fleet type validation failed: {fleet_type} not valid for {api}"
                )

            return is_valid

        except Exception as e:
            self._logger.error(
                f"Error validating fleet type {fleet_type} for AWS API {api}: {e}")
            return False

    def validate_template_configuration(self, template_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate a complete AWS template configuration.

        Args:
            template_config: Template configuration dictionary

        Returns:
            Validation result with 'valid', 'errors', and 'warnings' keys
        """
        errors = []
        warnings = []
        validated_fields = []

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
            self._validate_aws_specific_fields(
                template_config, errors, warnings, validated_fields)

        except Exception as e:
            self._logger.error(f"Error during AWS template validation: {e}")
            errors.append(f"Validation error: {str(e)}")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "validated_fields": validated_fields,
        }

    def _validate_aws_specific_fields(
        self,
        template_config: Dict[str, Any],
        errors: List[str],
        warnings: List[str],
        validated_fields: List[str],
    ) -> None:
        """
        Validate AWS-specific template fields.

        Args:
            template_config: Template configuration dictionary
            errors: List to append validation errors to
            warnings: List to append validation warnings to
            validated_fields: List to append validated field names to
        """
        # Validate AMI ID format
        image_id = template_config.get("image_id")
        if image_id:
            validated_fields.append("image_id")
            if not image_id.startswith("ami-"):
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


def create_aws_validation_adapter(logger: LoggingPort) -> AWSValidationAdapter:
    """
    Create AWS validation adapter with configuration.

    Args:
        logger: Logger for validation operations

    Returns:
        Configured AWS validation adapter
    """
    # Create a minimal config for validation purposes only
    # We don't need full AWS authentication just to validate fleet types
    try:
        config_manager = get_aws_config_manager()
        aws_config = config_manager.get_typed(AWSProviderConfig)
    except Exception as e:
        logger.debug(f"Could not load full AWS config for validation: {e}")
        # Create a minimal config with dummy auth for validation only
        from src.providers.aws.configuration.config import AWSProviderConfig

        aws_config = AWSProviderConfig(profile="validation-only")

    return AWSValidationAdapter(aws_config, logger)
