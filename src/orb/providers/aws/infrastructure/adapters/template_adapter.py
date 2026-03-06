"""
AWS Template Adapter - Consolidated AWS-specific template operations.

This module provides a integrated adapter for AWS-specific template operations,
consolidating validation, field extension, and reference resolution.
Follows the Adapter/Port pattern established in the codebase.
"""

import re
from typing import Any, Optional

from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.template_adapter_port import TemplateAdapterPort
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.dtos import TemplateDTO
from orb.providers.aws.exceptions.aws_exceptions import AWSValidationError
from orb.providers.aws.infrastructure.aws_client import AWSClient


class AWSTemplateAdapter(TemplateAdapterPort):
    """Consolidated adapter for AWS-specific template operations."""

    # Cache for SSM parameter resolution
    _ssm_parameter_cache: dict[str, str] = {}

    # AWS-specific field mappings and supported fields
    _AWS_SUPPORTED_FIELDS = [
        "image_id",
        "vm_type",
        "vm_types",
        "subnet_ids",
        "security_group_ids",
        "key_name",
        "iam_instance_profile",
        "user_data",
        "block_device_mappings",
        "placement",
        "monitoring",
        "ebs_optimized",
        "instance_initiated_shutdown_behavior",
        "disable_api_termination",
        "kernel_id",
        "ramdisk_id",
        "additional_info",
        "client_token",
        "network_interfaces",
        "dry_run",
        "private_ip_address",
        "secondary_private_ip_address_count",
        "associate_public_ip_address",
        "delete_on_termination",
        "description",
        "device_index",
        "groups",
        "ipv6_address_count",
        "ipv6_addresses",
        "network_interface_id",
        "private_ip_addresses",
        "subnet_id",
        "fleet_type",
        "spot_price",
        "target_capacity",
        "allocation_strategy",
        "diversified_allocation",
        "launch_template_configs",
        "on_demand_target_capacity",
        "spot_target_capacity",
    ]

    def __init__(
        self,
        template_config_manager: TemplateConfigurationManager,
        aws_client: AWSClient,
        logger: LoggingPort,
    ) -> None:
        """
        Initialize the adapter.

        Args:
            template_config_manager: Template configuration manager
            aws_client: AWS client instance
            logger: Logger for logging messages
        """
        self._template_config_manager = template_config_manager
        self._aws_client = aws_client
        self._logger = logger

    def validate_template(self, template: Template) -> list[str]:  # type: ignore[override]
        """
        Validate template for AWS-specific requirements.

        Args:
            template: Template domain entity to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # Validate required AWS fields
        errors.extend(self._validate_required_fields(template))

        # Validate AWS-specific field values
        field_errors = self.validate_field_values(template)
        errors.extend(field_errors.values())

        # Validate AWS configurations
        errors.extend(self._validate_aws_configurations(template))

        # Validate network configuration
        errors.extend(self._validate_network_configuration(template))

        return [error for error in errors if error]  # Filter out empty strings

    def extend_template_fields(self, template: Template) -> Template:
        """
        Extend template with AWS-specific fields and processing.

        Args:
            template: Template domain entity to extend

        Returns:
            Template with AWS-specific fields
        """
        # Set provider API based on template configuration
        if not template.provider_api:
            template.provider_api = self._determine_provider_api(template)

        # Extend with AWS-specific metadata
        if not template.metadata:
            template.metadata = {}

        if "aws" not in template.metadata:
            template.metadata["aws"] = {}

        # Add AWS-specific metadata
        template.metadata["aws"].update(
            {
                "fleet_type": getattr(template, "fleet_type", None),
                "supported_fields": self._AWS_SUPPORTED_FIELDS,
                "validation_enabled": True,
            }
        )

        return template

    def resolve_template_references(self, template: Template) -> Template:
        """
        Resolve AWS-specific references in template (e.g., AMI aliases, SSM parameters).

        Args:
            template: Template with potential references to resolve

        Returns:
            Template with resolved references
        """
        # Resolve AMI ID if it's an alias or SSM parameter
        if template.image_id:
            resolved_ami = self.resolve_ami_id(template.image_id)
            if resolved_ami != template.image_id:
                template.image_id = resolved_ami
                self._logger.info("Resolved AMI ID: %s", template.image_id)

        return template

    def get_supported_fields(self) -> list[str]:
        """
        Get list of fields supported by this AWS adapter.

        Returns:
            List of supported field names
        """
        return self._AWS_SUPPORTED_FIELDS.copy()

    def validate_field_values(self, template: Template) -> dict[str, str]:
        """
        Validate AWS-specific field values.

        Args:
            template: Template to validate

        Returns:
            Dictionary mapping field names to validation error messages
        """
        errors = {}

        # Validate image ID
        if not template.image_id:
            errors["image_id"] = "Image ID is required"
        elif not self._is_valid_ami_format(template.image_id):
            errors["image_id"] = f"Invalid AMI ID format: {template.image_id}"

        # Validate instance type(s)
        machine_types_map = template.machine_types
        abis = getattr(template, "abis_instance_requirements", None)
        if not (machine_types_map or abis):
            errors["machine_types"] = (
                "Either machine_types or abis_instance_requirements must be specified"
            )
        elif machine_types_map:
            for itype in machine_types_map.keys():
                if not self._is_valid_instance_type(itype):
                    errors["machine_types"] = f"Invalid instance type in machine_types: {itype}"
                    break

        # Validate subnet IDs
        if not template.subnet_ids or len(template.subnet_ids) == 0:
            errors["subnet_ids"] = "At least one subnet ID is required"
        else:
            for subnet_id in template.subnet_ids:
                if not self._is_valid_subnet_format(subnet_id):
                    errors["subnet_ids"] = f"Invalid subnet ID format: {subnet_id}"
                    break

        # Validate security group IDs
        if template.security_group_ids:
            for sg_id in template.security_group_ids:
                if not self._is_valid_security_group_format(sg_id):
                    errors["security_group_ids"] = f"Invalid security group ID format: {sg_id}"
                    break

        return errors

    def get_provider_api(self) -> str:
        """
        Get the provider API identifier for this adapter.

        Returns:
            Provider API identifier
        """
        return "EC2Fleet"  # Default AWS provider API

    # === PORT INTERFACE METHODS ===

    async def get_template_by_id(self, template_id: str) -> Optional[TemplateDTO]:  # type: ignore[override]
        """Get a template by its ID."""
        return await self._template_config_manager.get_template_by_id(template_id)

    async def get_all_templates(self) -> list[TemplateDTO]:  # type: ignore[override]
        """Get all available templates."""
        return await self._template_config_manager.get_all_templates()

    async def get_templates_by_provider_api(self, provider_api: str) -> list[TemplateDTO]:  # type: ignore[override]
        """Get templates filtered by provider API."""
        return await self._template_config_manager.get_templates_by_provider(provider_api)

    async def validate_template_dto(self, template: TemplateDTO) -> dict[str, Any]:
        """Validate a template configuration."""
        return await self._template_config_manager.validate_template(template)

    async def save_template(self, template: TemplateDTO) -> None:  # type: ignore[override]
        """Save a template."""
        await self._template_config_manager.save_template(template)

    async def delete_template(self, template_id: str) -> None:
        """Delete a template."""
        await self._template_config_manager.delete_template(template_id)

    def get_supported_provider_apis(self) -> list[str]:
        """Get the list of provider APIs supported by this adapter."""
        try:
            from orb.config.managers.configuration_manager import ConfigurationManager
            from orb.infrastructure.di.container import get_container

            container = get_container()
            config_manager = container.get(ConfigurationManager)
            raw_config = config_manager.get_raw_config()

            aws_handlers = (
                raw_config.get("provider", {})
                .get("provider_defaults", {})
                .get("aws", {})
                .get("handlers", {})
            )

            return list(aws_handlers.keys())
        except Exception as e:
            self._logger.error("Error getting supported AWS APIs: %s", e)
            return []

    def get_adapter_info(self) -> dict[str, Any]:
        """Get information about this adapter."""
        return {
            "adapter_name": "AWSTemplateAdapter",
            "provider_type": "aws",
            "supported_apis": self.get_supported_provider_apis(),
            "supported_fields": self._AWS_SUPPORTED_FIELDS,
            "features": [
                "ami_resolution",
                "ssm_parameter_resolution",
                "field_validation",
                "network_validation",
                "fleet_type_support",
            ],
        }

    # === LEGACY COMPATIBILITY METHODS ===

    def validate_aws_configuration(self, template: Template) -> dict[str, str]:
        """
        Validate AWS-specific configuration in template.
        Legacy method for backward compatibility.

        Args:
            template: Template domain entity

        Returns:
            Dictionary of field names to error messages
        """
        return self.validate_field_values(template)

    def resolve_ami_id(self, ami_id_or_alias: str) -> str:
        """
        Resolve AMI ID from alias, SSM parameter path, or direct ID.

        Args:
            ami_id_or_alias: AMI ID, alias, or SSM parameter path

        Returns:
            Resolved AMI ID

        Raises:
            AWSValidationError: If AMI ID cannot be resolved
        """
        # If it's already a valid AMI ID, return as-is
        if self._is_valid_ami_format(ami_id_or_alias):
            return ami_id_or_alias

        # Check if it's an SSM parameter path
        if ami_id_or_alias.startswith("/"):
            try:
                resolved_ami = self._resolve_ssm_parameter(ami_id_or_alias)
                if self._is_valid_ami_format(resolved_ami):
                    return resolved_ami
                else:
                    raise AWSValidationError(
                        f"SSM parameter {ami_id_or_alias} does not contain a valid AMI ID: {resolved_ami}"
                    )
            except Exception as e:
                self._logger.error("Failed to resolve SSM parameter %s: %s", ami_id_or_alias, e)
                raise AWSValidationError(
                    f"Failed to resolve AMI ID from SSM parameter: {ami_id_or_alias}"
                )

        # Handle known aliases (could be extended)
        alias_mappings = {
            "amazon-linux-2": "/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2",
            "ubuntu-20.04": "/aws/service/canonical/ubuntu/server/20.04/stable/current/amd64/hvm/ebs-gp2/ami-id",
            "ubuntu-22.04": "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id",
        }

        if ami_id_or_alias.lower() in alias_mappings:
            ssm_path = alias_mappings[ami_id_or_alias.lower()]
            return self.resolve_ami_id(ssm_path)  # Recursive call with SSM path

        # If we can't resolve it, return as-is and let AWS validation handle it
        self._logger.warning("Could not resolve AMI alias: %s", ami_id_or_alias)
        return ami_id_or_alias

    def validate_ami_id(self, ami_id: str) -> bool:
        """
        Validate that an AMI ID exists and is usable.

        Args:
            ami_id: AMI ID to validate

        Returns:
            True if AMI is valid and usable, False otherwise
        """
        try:
            ec2_client = self._aws_client.ec2_client
            response = ec2_client.describe_images(ImageIds=[ami_id])

            if not response.get("Images"):
                return False

            image = response["Images"][0]
            return image.get("State") == "available"

        except Exception as e:
            self._logger.error("Failed to validate AMI ID %s: %s", ami_id, e)
            return False

    # === PRIVATE HELPER METHODS ===

    def _validate_required_fields(self, template: Template) -> list[str]:
        """Validate required AWS fields."""
        errors = []

        if not template.image_id:
            errors.append("Image ID is required for AWS templates")

        if not template.machine_types:
            errors.append("Machine types are required for AWS templates")

        if not template.subnet_ids or len(template.subnet_ids) == 0:
            errors.append("At least one subnet ID is required for AWS templates")

        return errors

    def _validate_aws_configurations(self, template: Template) -> list[str]:
        """Validate AWS-specific configurations."""
        errors = []

        # Validate fleet type if specified
        fleet_type = getattr(template, "fleet_type", None)
        if fleet_type and fleet_type not in ["instant", "request", "maintain"]:
            errors.append(
                f"Invalid fleet type: {fleet_type}. Must be 'instant', 'request', or 'maintain'"
            )

        # Validate spot price if specified
        spot_price = getattr(template, "spot_price", None)
        if spot_price:
            try:
                float(spot_price)
            except (ValueError, TypeError):
                errors.append(f"Invalid spot price format: {spot_price}")

        return errors

    def _validate_network_configuration(self, template: Template) -> list[str]:
        """Validate network configuration."""
        errors = []

        # Validate that subnets and security groups are compatible
        if template.subnet_ids and template.security_group_ids:
            # This could be extended to validate VPC compatibility
            pass

        return errors

    def _determine_provider_api(self, template: Template) -> str:
        """Determine the appropriate provider API based on template configuration."""
        # Check for spot fleet indicators
        if (
            getattr(template, "spot_price", None)
            or getattr(template, "fleet_type", None) == "request"
        ):
            return "SpotFleet"

        # Default to EC2Fleet
        return "EC2Fleet"

    def _resolve_ssm_parameter(self, parameter_path: str) -> str:
        """
        Resolve SSM parameter value with caching.

        Args:
            parameter_path: SSM parameter path

        Returns:
            Parameter value

        Raises:
            Exception: If parameter cannot be resolved
        """
        # Check cache first
        if parameter_path in self._ssm_parameter_cache:
            return self._ssm_parameter_cache[parameter_path]

        try:
            ssm_client = self._aws_client.ssm_client

            def get_parameter_func():
                """Retrieve parameter value from SSM."""
                response = ssm_client.get_parameter(Name=parameter_path)
                return response["Parameter"]["Value"]

            # Use retry logic if available
            parameter_value = get_parameter_func()

            # Cache the result
            self._ssm_parameter_cache[parameter_path] = parameter_value

            return parameter_value

        except Exception as e:
            self._logger.error("Failed to resolve SSM parameter %s: %s", parameter_path, e)
            raise

    def _is_valid_ami_format(self, ami_id: str) -> bool:
        """Check if string matches AMI ID format."""
        return bool(re.match(r"^ami-[0-9a-f]{8,17}$", ami_id))

    def _is_valid_instance_type(self, instance_type: str) -> bool:
        """Validate instance type format."""
        # Basic validation - could be extended with actual AWS instance type list
        return bool(re.match(r"^[a-z0-9]+\.[a-z0-9]+$", instance_type))

    def _is_valid_subnet_format(self, subnet_id: str) -> bool:
        """Check if string matches subnet ID format."""
        return bool(re.match(r"^subnet-[0-9a-f]{8,17}$", subnet_id))

    def _is_valid_security_group_format(self, sg_id: str) -> bool:
        """Check if string matches security group ID format."""
        return bool(re.match(r"^sg-[0-9a-f]{8,17}$", sg_id))


def create_aws_template_adapter(
    aws_client: AWSClient, logger: LoggingPort, config: ConfigurationPort
) -> AWSTemplateAdapter:
    """
    Create AWS template adapter.

    Args:
        aws_client: AWS client instance
        logger: Logger instance
        config: Configuration instance

    Returns:
        AWS template adapter instance
    """
    from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager

    template_config_manager = TemplateConfigurationManager(aws_client, logger)  # type: ignore[arg-type]
    return AWSTemplateAdapter(template_config_manager, aws_client, logger)
