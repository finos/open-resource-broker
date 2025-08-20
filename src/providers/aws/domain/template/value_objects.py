"""AWS-specific value objects and domain extensions."""

import re
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import ConfigDict, field_validator, model_validator

# Import core domain primitives
from domain.base.value_objects import (
    ARN,
    AllocationStrategy,
    InstanceType,
    PriceType,
    ResourceId,
    Tags,
    ValueObject,
)

# Import domain protocols


class ResourceId(ResourceId):
    """Base class for AWS resource IDs with AWS-specific validation."""

    pattern_key: ClassVar[str] = ""

    @field_validator("value")
    @classmethod
    def validate_format(cls, v: str) -> str:
        """Validate AWS resource ID format."""
        # Get pattern from AWS configuration
        from providers.aws.configuration.config import get_aws_config_manager
        from providers.aws.configuration.validator import AWSNamingConfig

        config = get_aws_config_manager().get_typed(AWSNamingConfig)
        pattern = config.patterns.get(cls.pattern_key)

        # Fall back to class pattern if not in config
        if not pattern:
            raise ValueError(f"Pattern for {cls.resource_type} not found in AWS configuration")

        if not re.match(pattern, v):
            raise ValueError(f"Invalid AWS {cls.resource_type} ID format: {v}")
        return v


class AWSSubnetId(ResourceId):
    """AWS Subnet ID value object."""

    resource_type: ClassVar[str] = "Subnet"
    pattern_key: ClassVar[str] = "subnet"


class AWSSecurityGroupId(ResourceId):
    """AWS Security Group ID value object."""

    resource_type: ClassVar[str] = "Security Group"
    pattern_key: ClassVar[str] = "security_group"


class InstanceId(ResourceId):
    """AWS Instance ID value object."""

    resource_type: ClassVar[str] = "Instance"
    pattern_key: ClassVar[str] = "ec2_instance"

    def to_aws_format(self) -> str:
        """Convert to AWS API format."""
        return self.value


class AWSImageId(ResourceId):
    """AWS AMI ID value object."""

    resource_type: ClassVar[str] = "AMI"
    pattern_key: ClassVar[str] = "ami"

    def to_aws_format(self) -> str:
        """Convert to AWS API format."""
        return self.value


class AWSFleetId(ResourceId):
    """AWS Fleet ID value object."""

    resource_type: ClassVar[str] = "Fleet"
    pattern_key: ClassVar[str] = "ec2_fleet"


class AWSLaunchTemplateId(ResourceId):
    """AWS Launch Template ID value object."""

    resource_type: ClassVar[str] = "Launch Template"
    pattern_key: ClassVar[str] = "launch_template"


class AWSInstanceType(InstanceType):
    """AWS Instance Type value object with AWS-specific validation."""

    @field_validator("value")
    @classmethod
    def validate_instance_type(cls, v: str) -> str:
        """Validate AWS instance type format."""
        # Get pattern from AWS configuration
        from providers.aws.configuration.config import get_aws_config_manager
        from providers.aws.configuration.validator import AWSNamingConfig

        config = get_aws_config_manager().get_typed(AWSNamingConfig)
        pattern = config.patterns["instance_type"]

        if not re.match(pattern, v):
            raise ValueError(f"Invalid AWS instance type format: {v}")
        return v

    @property
    def family(self) -> str:
        """Get the AWS instance family (e.g., t2, m5)."""
        return self.value.split(".")[0]

    @property
    def size(self) -> str:
        """Get the AWS instance size (e.g., micro, large)."""
        return self.value.split(".")[1]


class AWSTags(Tags):
    """AWS resource tags with AWS-specific validation."""

    @field_validator("tags")
    @classmethod
    def validate_aws_tags(cls, v: Dict[str, str]) -> Dict[str, str]:
        """Validate AWS tags format and constraints."""
        # Get AWS tag validation rules from configuration
        from providers.aws.configuration.config import get_aws_config_manager
        from providers.aws.configuration.validator import AWSNamingConfig

        config = get_aws_config_manager().get_typed(AWSNamingConfig)

        for key, value in v.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("AWS tags must be strings")

            # Use AWS limits from configuration
            if len(key) > config.limits.tag_key_length:
                raise ValueError(
                    f"AWS tag key length exceeds limit of {config.limits.tag_key_length}"
                )
            if len(value) > config.limits.tag_value_length:
                raise ValueError(
                    f"AWS tag value length exceeds limit of {config.limits.tag_value_length}"
                )

            # Use AWS pattern from configuration
            if not re.match(config.patterns["tag_key"], key):
                raise ValueError(f"Invalid AWS tag key format: {key}")
        return v

    def to_aws_format(self) -> List[Dict[str, str]]:
        """Convert to AWS API format."""
        return [{"Key": k, "Value": v} for k, v in self.values.items()]


class AWSARN(ARN):
    """AWS ARN value object with AWS-specific parsing."""

    partition: Optional[str] = None
    service: Optional[str] = None
    region: Optional[str] = None
    account_id: Optional[str] = None
    resource: Optional[str] = None

    @field_validator("value")
    @classmethod
    def validate_arn(cls, v: str) -> str:
        """Validate AWS ARN format."""
        # Get pattern from AWS configuration
        from providers.aws.configuration.config import get_aws_config_manager
        from providers.aws.configuration.validator import AWSNamingConfig

        config = get_aws_config_manager().get_typed(AWSNamingConfig)
        pattern = config.patterns["arn"]

        if not re.match(pattern, v):
            raise ValueError(f"Invalid AWS ARN format: {v}")
        return v

    def model_post_init(self, __context: Any) -> None:
        """Parse AWS ARN components after initialization."""
        parts = self.value.split(":")
        if len(parts) >= 6:
            object.__setattr__(self, "partition", parts[1])
            object.__setattr__(self, "service", parts[2])
            object.__setattr__(self, "region", parts[3])
            object.__setattr__(self, "account_id", parts[4])
            object.__setattr__(self, "resource", ":".join(parts[5:]))


class ProviderApi(str, Enum):
    """AWS-specific provider API types - dynamically loaded from configuration."""

    @classmethod
    def _missing_(cls, value) -> None:
        """Handle missing enum values by checking configuration."""
        # Get valid APIs from configuration
        try:
            from config.manager import get_config_manager

            config_manager = get_config_manager()
            raw_config = config_manager.get_raw_config()

            # Navigate to AWS handlers in configuration
            aws_handlers = (
                raw_config.get("provider", {})
                .get("provider_defaults", {})
                .get("aws", {})
                .get("handlers", {})
            )

            if value in aws_handlers:
                # Dynamically create enum member
                new_member = object.__new__(cls)
                new_member._name_ = value
                new_member._value_ = value
                return new_member
        except Exception:
            # Fall through to hardcoded fallback
            pass  # nosec B110

        # Fallback to hardcoded values for safety
        fallback_values = {
            "EC2Fleet": "EC2Fleet",
            "SpotFleet": "SpotFleet",
            "ASG": "ASG",
            "RunInstances": "RunInstances",
        }

        if value in fallback_values:
            new_member = object.__new__(cls)
            new_member._name_ = value
            new_member._value_ = value
            return new_member

        return None

    # Define common values as class attributes for IDE support
    EC2_FLEET = "EC2Fleet"
    SPOT_FLEET = "SpotFleet"
    ASG = "ASG"
    RUN_INSTANCES = "RunInstances"


class AWSFleetType(str, Enum):
    """AWS Fleet type - dynamically loaded from configuration."""

    @classmethod
    def _missing_(cls, value) -> None:
        """Handle missing enum values by checking configuration."""
        # Get valid fleet types from configuration
        try:
            from config.manager import get_config_manager

            config_manager = get_config_manager()
            raw_config = config_manager.get_raw_config()

            # Check all handlers for supported fleet types
            aws_handlers = (
                raw_config.get("provider", {})
                .get("provider_defaults", {})
                .get("aws", {})
                .get("handlers", {})
            )

            # Collect all unique fleet types from all handlers
            all_fleet_types = set()
            for handler_config in aws_handlers.values():
                fleet_types = handler_config.get("supported_fleet_types", [])
                all_fleet_types.update(fleet_types)

            if value in all_fleet_types:
                # Dynamically create enum member
                new_member = object.__new__(cls)
                new_member._name_ = value.upper()
                new_member._value_ = value
                return new_member
        except Exception:
            # Fall through to hardcoded fallback
            pass  # nosec B110

        # Fallback to hardcoded values for safety
        fallback_values = {
            "instant": "instant",
            "request": "request",
            "maintain": "maintain",
        }

        if value in fallback_values:
            new_member = object.__new__(cls)
            new_member._name_ = value.upper()
            new_member._value_ = value
            return new_member

        return None

    # Define common values as class attributes for IDE support
    INSTANT = "instant"  # EC2 Fleet only
    REQUEST = "request"  # Both EC2 Fleet and Spot Fleet
    MAINTAIN = "maintain"  # Both EC2 Fleet and Spot Fleet


class AWSAllocationStrategy:
    """AWS-specific allocation strategy wrapper with AWS API formatting."""

    def __init__(self, strategy: AllocationStrategy) -> None:
        """Initialize the instance."""
        self._strategy = strategy

    @property
    def value(self) -> str:
        """Get the strategy value."""
        return self._strategy.value

    @classmethod
    def from_core(cls, strategy: AllocationStrategy) -> "AWSAllocationStrategy":
        """Create from core allocation strategy."""
        return cls(strategy)

    def to_ec2_fleet_format(self) -> str:
        """Convert to EC2 Fleet API format."""
        mapping = {
            AllocationStrategy.CAPACITY_OPTIMIZED: "capacity-optimized",
            AllocationStrategy.DIVERSIFIED: "diversified",
            AllocationStrategy.LOWEST_PRICE: "lowest-price",
            AllocationStrategy.PRICE_CAPACITY_OPTIMIZED: "price-capacity-optimized",
        }
        return mapping.get(self._strategy, "lowest-price")

    def to_spot_fleet_format(self) -> str:
        """Convert to Spot Fleet API format."""
        mapping = {
            AllocationStrategy.CAPACITY_OPTIMIZED: "capacityOptimized",
            AllocationStrategy.DIVERSIFIED: "diversified",
            AllocationStrategy.LOWEST_PRICE: "lowestPrice",
            AllocationStrategy.PRICE_CAPACITY_OPTIMIZED: "priceCapacityOptimized",
        }
        return mapping.get(self._strategy, "lowestPrice")

    def to_asg_format(self) -> str:
        """Convert to Auto Scaling Group API format."""
        mapping = {
            AllocationStrategy.CAPACITY_OPTIMIZED: "capacity-optimized",
            AllocationStrategy.DIVERSIFIED: "diversified",
            AllocationStrategy.LOWEST_PRICE: "lowest-price",
            AllocationStrategy.PRICE_CAPACITY_OPTIMIZED: "price-capacity-optimized",
        }
        return mapping.get(self._strategy, "lowest-price")


class AWSConfiguration(ValueObject):
    """AWS-specific configuration value object - clean domain object without infrastructure dependencies."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    handler_type: ProviderApi
    fleet_type: Optional[AWSFleetType] = None
    # Use core enum, not wrapper
    allocation_strategy: Optional[AllocationStrategy] = None
    price_type: Optional[PriceType] = None
    subnet_ids: List[AWSSubnetId] = []
    security_group_ids: List[AWSSecurityGroupId] = []

    @model_validator(mode="after")
    def validate_aws_configuration(self) -> "AWSConfiguration":
        """Validate AWS-specific configuration - basic domain validation only."""
        # Set default fleet type if not provided
        if not self.fleet_type:
            # Use simple default without configuration dependency
            object.__setattr__(self, "fleet_type", AWSFleetType.REQUEST)

        # Set default allocation strategy if not provided
        if not self.allocation_strategy:
            object.__setattr__(self, "allocation_strategy", AllocationStrategy.LOWEST_PRICE)

        # Set default price type if not provided
        if not self.price_type:
            object.__setattr__(self, "price_type", PriceType.ONDEMAND)

        return self

    def to_aws_api_format(self) -> Dict[str, Any]:
        """Convert to AWS API format."""
        return {
            "handler_type": self.handler_type.value,
            "fleet_type": self.fleet_type.value if self.fleet_type else None,
            "allocation_strategy": (
                self.allocation_strategy.value if self.allocation_strategy else None
            ),
            "price_type": self.price_type.value if self.price_type else None,
            "subnet_ids": [subnet.value for subnet in self.subnet_ids],
            "security_group_ids": [sg.value for sg in self.security_group_ids],
        }
