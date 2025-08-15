"""AWS provider adapter implementation."""

import re
from typing import Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.base.provider_interfaces import (
    ProviderInstanceState,
    ProviderLaunchTemplate,
    ProviderResourceIdentifier,
    ProviderResourceTag,
    ProviderResourceValidator,
    ProviderStateMapper,
    ProviderType,
)


class AWSStateMapper:
    """AWS-specific state mapper."""

    def map_to_domain_state(self, aws_state: str) -> ProviderInstanceState:
        """Map AWS instance state to domain state."""
        state_map = {
            "pending": ProviderInstanceState.PENDING,
            "running": ProviderInstanceState.RUNNING,
            "stopping": ProviderInstanceState.STOPPING,
            "stopped": ProviderInstanceState.STOPPED,
            "shutting-down": ProviderInstanceState.SHUTTING_DOWN,
            "terminated": ProviderInstanceState.TERMINATED,
        }
        return state_map.get(aws_state.lower(), ProviderInstanceState.UNKNOWN)

    def map_from_domain_state(self, domain_state: ProviderInstanceState) -> str:
        """Map domain state to AWS instance state."""
        state_map = {
            ProviderInstanceState.PENDING: "pending",
            ProviderInstanceState.RUNNING: "running",
            ProviderInstanceState.STOPPING: "stopping",
            ProviderInstanceState.STOPPED: "stopped",
            ProviderInstanceState.SHUTTING_DOWN: "shutting-down",
            ProviderInstanceState.TERMINATED: "terminated",
            ProviderInstanceState.UNKNOWN: "unknown",
        }
        return state_map.get(domain_state, "unknown")


class AWSResourceValidator:
    """AWS-specific resource validator."""

    def validate_resource_identifier(self, identifier: str, resource_type: str) -> bool:
        """Validate AWS resource identifier format."""
        if not identifier:
            return False

        # AWS resource ID patterns
        patterns = {
            "instance": r"^i-[0-9a-f]{8,17}$",
            "volume": r"^vol-[0-9a-f]{8,17}$",
            "subnet": r"^subnet-[0-9a-f]{8,17}$",
            "security_group": r"^sg-[0-9a-f]{8,17}$",
            "vpc": r"^vpc-[0-9a-f]{8,17}$",
            "ami": r"^ami-[0-9a-f]{8,17}$",
            "launch_template": r"^lt-[0-9a-f]{8,17}$",
            "key_pair": r"^[a-zA-Z0-9\-_]{1,255}$",
            "arn": r"^arn:aws:[a-zA-Z0-9-]+:[a-zA-Z0-9-]*:[0-9]*:.*$",
        }

        pattern = patterns.get(resource_type.lower())
        if not pattern:
            # For unknown resource types, just check it's not empty
            return bool(identifier.strip())

        return bool(re.match(pattern, identifier))

    def validate_tag(self, tag: ProviderResourceTag) -> bool:
        """Validate AWS-specific tag constraints."""
        # AWS tag key validation
        if not tag.key or len(tag.key) > 128:
            return False
        if tag.key.startswith("aws:"):
            return False

        # AWS tag value validation
        if len(tag.value) > 256:
            return False

        # AWS doesn't allow certain characters
        invalid_chars = ["<", ">", "&", '"', "'"]
        if any(char in tag.key or char in tag.value for char in invalid_chars):
            return False

        return True

    def validate_launch_template(self, template: ProviderLaunchTemplate) -> bool:
        """Validate AWS launch template format."""
        # Validate template ID format
        if not self.validate_resource_identifier(template.template_id, "launch_template"):
            return False

        # Validate version if provided
        if template.version and template.version not in ["$Latest", "$Default"]:
            # AWS launch template versions can be numbers or $Latest or $Default
            try:
                int(template.version)
            except ValueError:
                return False

        return True


@injectable
class AWSProviderAdapter:
    """AWS provider adapter implementation."""

    def __init__(self, logger: LoggingPort):
        """Initialize the instance."""
        self._state_mapper = AWSStateMapper()
        self._resource_validator = AWSResourceValidator()
        self._logger = logger

    @property
    def provider_type(self) -> ProviderType:
        """Get the provider type."""
        return ProviderType.AWS

    @property
    def state_mapper(self) -> ProviderStateMapper:
        """Get the state mapper for AWS."""
        return self._state_mapper

    @property
    def resource_validator(self) -> ProviderResourceValidator:
        """Get the resource validator for AWS."""
        return self._resource_validator

    def create_resource_identifier(
        self, resource_type: str, identifier: str, region: Optional[str] = None
    ) -> ProviderResourceIdentifier:
        """Create an AWS resource identifier."""
        if not self._resource_validator.validate_resource_identifier(identifier, resource_type):
            self._logger.warning(f"Invalid AWS {resource_type} identifier: {identifier}")
            raise ValueError(f"Invalid AWS {resource_type} identifier: {identifier}")

        return ProviderResourceIdentifier(
            provider_type=ProviderType.AWS,
            resource_type=resource_type,
            identifier=identifier,
            region=region,
        )

    def create_launch_template(
        self, template_id: str, version: Optional[str] = None
    ) -> ProviderLaunchTemplate:
        """Create an AWS launch template."""
        template = ProviderLaunchTemplate(template_id=template_id, version=version)

        if not self._resource_validator.validate_launch_template(template):
            self._logger.warning(f"Invalid AWS launch template: {template_id}")
            raise ValueError(f"Invalid AWS launch template: {template_id}")

        return template
