"""AWS Validation Adapter - AWS-specific implementation of provider validation."""

from enum import Enum
from typing import Any

from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_validation_port import BaseProviderValidationAdapter
from orb.infrastructure.di.injectable import injectable
from orb.providers.aws.configuration.validator import (
    AWSHandlerConfig,
    AWSProviderConfig,
)

# Static handler capability data — no runtime config needed.
_HANDLER_CONFIG = AWSHandlerConfig()

# A row reaches this adapter in either shape: snake_case attributes (a Template's
# vars, or a mapped row) or camelCase keys (a HostFactory file row).
_PROVIDER_API = ("provider_api", "providerApi")
_FLEET_TYPE = ("fleet_type", "fleetType")
_IMAGE = ("machine_image", "image_id", "imageId")
_SUBNETS = ("subnet_ids", "subnetIds", "subnet_id", "subnetId")
_SECURITY_GROUPS = (
    "security_group_ids",
    "securityGroupIds",
    "security_group_id",
    "securityGroupId",
)
_PERCENT_ON_DEMAND = ("percent_on_demand", "percentOnDemand")
_MACHINE_TYPES = ("machine_types", "vmTypes", "vmType", "instance_type")

# Accepted image forms: AMI id, SSM parameter path, and the ssm: / resolve:ssm: references.
_IMAGE_PREFIXES = ("ami-", "/", "ssm:", "resolve:ssm:")
# A MicroVM template names its image by ARN rather than by AMI.
_MICROVM_IMAGE_PREFIXES = (*_IMAGE_PREFIXES, "arn:")


def _values(row: dict[str, Any], aliases: tuple[str, ...]) -> list[Any]:
    """Return every distinct non-empty value held under any of the key aliases.

    A row with defaults applied can carry the same field under two spellings (the
    row's own camelCase key and a snake_case default), so each one is checked
    rather than letting one hide the other.
    """
    found: list[Any] = []
    for key in aliases:
        value = row.get(key)
        if value in (None, "", [], (), {}) or value in found:
            continue
        found.append(value)
    return found


def _as_list(values: list[Any]) -> list[str]:
    """Flatten lists, tuples, dicts (keys) and comma-joined strings to distinct clean strings."""
    items: list[str] = []
    for value in values:
        parts = value.split(",") if isinstance(value, str) else value
        items.extend(str(part).strip() for part in parts if str(part).strip())
    return list(dict.fromkeys(items))


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
            for provider_api in _as_list(_values(template_config, _PROVIDER_API)):
                validated_fields.append("provider_api")
                if not self.validate_provider_api(provider_api):
                    errors.append(f"Unsupported AWS provider API: {provider_api}")
                else:
                    self._validate_fleet_type(
                        template_config, provider_api, errors, warnings, validated_fields
                    )

            self._validate_aws_specific_fields(template_config, errors, warnings, validated_fields)

        except Exception as e:
            self._logger.error("Error during AWS template validation: %s", e)
            errors.append(f"Validation error: {e!s}")

        errors = list(dict.fromkeys(errors))
        warnings = list(dict.fromkeys(warnings))
        validated_fields = list(dict.fromkeys(validated_fields))

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "validated_fields": validated_fields,
        }

    def _validate_fleet_type(
        self,
        template_config: dict[str, Any],
        provider_api: str,
        errors: list[str],
        warnings: list[str],
        validated_fields: list[str],
    ) -> None:
        """Check the fleet type against the API's capabilities.

        Only the fleet APIs read ``fleet_type``; on the other APIs a value is
        ignored, so it is reported as a warning.
        """
        for raw in _values(template_config, _FLEET_TYPE):
            fleet_type = str(raw.value if isinstance(raw, Enum) else raw)
            validated_fields.append("fleet_type")
            if not self.get_valid_fleet_types_for_api(provider_api):
                warnings.append(f"Fleet type '{fleet_type}' is ignored by AWS API '{provider_api}'")
            elif not self.validate_fleet_type_for_api(fleet_type, provider_api):
                errors.append(
                    f"Fleet type '{fleet_type}' is not compatible with AWS API '{provider_api}'"
                )

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
        # AWS requires an image: an AMI id, an SSM parameter path, or an ssm: reference.
        images = [str(image) for image in _values(template_config, _IMAGE)]
        microvm = "MicroVM" in _as_list(_values(template_config, _PROVIDER_API))
        accepted = _MICROVM_IMAGE_PREFIXES if microvm else _IMAGE_PREFIXES
        if not images:
            errors.append("image_id is required for AWS templates")
        for image in images:
            validated_fields.append("image_id")
            if not image.startswith(accepted):
                errors.append(f"Invalid AWS AMI ID format: {image}")

        machine_types = _as_list(_values(template_config, _MACHINE_TYPES))
        if machine_types:
            validated_fields.append("instance_type")
            warnings.extend(
                f"Uncommon AWS instance type: {instance_type}"
                for instance_type in machine_types
                if not self._is_valid_instance_type(instance_type)
            )

        for aliases, field_name, prefix, label in (
            (_SUBNETS, "subnet_ids", "subnet-", "subnet"),
            (_SECURITY_GROUPS, "security_group_ids", "sg-", "security group"),
        ):
            ids = _as_list(_values(template_config, aliases))
            if ids:
                validated_fields.append(field_name)
                errors.extend(
                    f"Invalid AWS {label} ID format: {item}"
                    for item in ids
                    if not item.startswith(prefix)
                )

        for percent in _values(template_config, _PERCENT_ON_DEMAND):
            validated_fields.append("percent_on_demand")
            if not self._percent_in_range(percent):
                errors.append("percent_on_demand must be between 0 and 100")

    @staticmethod
    def _percent_in_range(value: Any) -> bool:
        """True when *value* is a number, or a numeric string, from 0 to 100."""
        if isinstance(value, bool):
            return False
        try:
            return 0 <= float(value) <= 100
        except (TypeError, ValueError):
            return False

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

        family, _size = instance_type.split(".", 1)

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
