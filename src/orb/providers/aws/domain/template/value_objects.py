"""AWS-specific value objects and domain extensions."""

import re
from enum import Enum
from typing import Any, ClassVar, Optional

from pydantic import ConfigDict, field_validator, model_validator

# Import core domain primitives
from orb.domain.base.value_objects import (
    ARN,
    InstanceType,
    PriceType,
    ResourceId as _BaseResourceId,
    Tags,
    ValueObject,
)

# Import domain protocols


class ResourceId(_BaseResourceId):
    """Base class for AWS resource IDs with AWS-specific validation."""

    pattern_key: ClassVar[str] = ""

    @field_validator("value")
    @classmethod
    def validate_format(cls, v: str) -> str:
        """Validate AWS resource ID format."""
        # Get pattern from AWS configuration
        from orb.providers.aws.configuration.validator import (
            AWSNamingConfig,
            get_aws_config_manager,
        )

        config: AWSNamingConfig = get_aws_config_manager().get_typed(AWSNamingConfig)  # type: ignore[assignment]
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
        from orb.providers.aws.configuration.validator import (
            AWSNamingConfig,
            get_aws_config_manager,
        )

        config: AWSNamingConfig = get_aws_config_manager().get_typed(AWSNamingConfig)  # type: ignore[assignment]
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
    def validate_aws_tags(cls, v: dict[str, str]) -> dict[str, str]:
        """Validate AWS tags format and constraints."""
        # Get AWS tag validation rules from configuration
        from orb.providers.aws.configuration.validator import (
            AWSNamingConfig,
            get_aws_config_manager,
        )

        config: AWSNamingConfig = get_aws_config_manager().get_typed(AWSNamingConfig)  # type: ignore[assignment]

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

    def to_aws_format(self) -> list[dict[str, str]]:
        """Convert to AWS API format."""
        return [{"Key": k, "Value": v} for k, v in self.tags.items()]


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
        from orb.providers.aws.configuration.validator import (
            AWSNamingConfig,
            get_aws_config_manager,
        )

        config: AWSNamingConfig = get_aws_config_manager().get_typed(AWSNamingConfig)  # type: ignore[assignment]
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
    def _missing_(cls, value: object) -> Optional["ProviderApi"]:
        """Handle missing enum values for raw string lookups."""
        if not isinstance(value, str):
            return None

        known_values = {
            "EC2Fleet": "EC2Fleet",
            "SpotFleet": "SpotFleet",
            "ASG": "ASG",
            "RunInstances": "RunInstances",
        }

        if value in known_values:
            new_member = str.__new__(cls, value)
            new_member._name_ = value  # type: ignore[misc]
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
    def _missing_(cls, value: object) -> Optional["AWSFleetType"]:
        """Handle missing enum values for raw string lookups."""
        if not isinstance(value, str):
            return None

        known_values = {
            "instant": "instant",
            "request": "request",
            "maintain": "maintain",
        }

        if value in known_values:
            new_member = str.__new__(cls, value)
            new_member._name_ = value.upper()  # type: ignore[misc]
            new_member._value_ = value
            return new_member

        return None

    # Define common values as class attributes for IDE support
    INSTANT = "instant"  # EC2 Fleet only
    REQUEST = "request"  # Both EC2 Fleet and Spot Fleet
    MAINTAIN = "maintain"  # Both EC2 Fleet and Spot Fleet


class AWSAllocationStrategy:
    """AWS-specific allocation strategy with AWS API formatting.

    Accepts any allocation strategy string format (camelCase, hyphenated, or snake_case)
    and normalises it to the canonical camelCase form internally.
    """

    _FORMAT_MAPS: ClassVar[dict[str, dict[str, str]]] = {
        "ec2_fleet": {
            "capacityOptimized": "capacity-optimized",
            "capacityOptimizedPrioritized": "capacity-optimized-prioritized",
            "diversified": "diversified",
            "lowestPrice": "lowest-price",
            "priceCapacityOptimized": "price-capacity-optimized",
            "prioritized": "prioritized",
        },
        "spot_fleet": {
            "capacityOptimized": "capacityOptimized",
            "capacityOptimizedPrioritized": "capacityOptimizedPrioritized",
            "diversified": "diversified",
            "lowestPrice": "lowestPrice",
            "priceCapacityOptimized": "priceCapacityOptimized",
        },
        "asg": {
            "capacityOptimized": "capacity-optimized",
            "capacityOptimizedPrioritized": "capacity-optimized-prioritized",
            "diversified": "diversified",
            "lowestPrice": "lowest-price",
            "priceCapacityOptimized": "price-capacity-optimized",
        },
    }

    _DEFAULTS: ClassVar[dict[str, str]] = {
        "ec2_fleet": "lowest-price",
        "spot_fleet": "lowestPrice",
        "asg": "lowest-price",
    }

    def __init__(self, strategy: str) -> None:
        """Initialise from any accepted strategy string format."""
        self._canonical = normalise_allocation_strategy(strategy)

    @property
    def value(self) -> str:
        """Return the canonical camelCase strategy value."""
        return self._canonical

    @classmethod
    def from_string(cls, strategy: str) -> "AWSAllocationStrategy":
        """Create from any strategy string (camelCase, hyphenated, or snake_case)."""
        return cls(strategy)

    @classmethod
    def from_core(cls, strategy: Any) -> "AWSAllocationStrategy":
        """Create from a legacy AllocationStrategy enum or plain string.

        Kept for backwards compatibility — prefer from_string() for new code.
        """
        return cls(str(strategy.value) if hasattr(strategy, "value") else str(strategy))

    def to_api_format(self, api: str) -> str:
        """Convert to the wire format for the given AWS API."""
        fmt_map = self._FORMAT_MAPS.get(api, {})
        return fmt_map.get(self._canonical, self._DEFAULTS.get(api, self._canonical))

    def to_ec2_fleet_format(self) -> str:
        """Convert to EC2 Fleet API format (hyphenated)."""
        return self.to_api_format("ec2_fleet")

    def to_spot_fleet_format(self) -> str:
        """Convert to Spot Fleet API format (camelCase)."""
        return self.to_api_format("spot_fleet")

    def to_asg_format(self) -> str:
        """Convert to Auto Scaling Group API format (hyphenated)."""
        return self.to_api_format("asg")


# Canonical (camelCase) allocation strategy values — the authoritative set used on disk
# and by the HF/SpotFleet wire format.
CANONICAL_ALLOCATION_STRATEGIES: frozenset[str] = frozenset(
    {
        "capacityOptimized",
        "capacityOptimizedPrioritized",
        "diversified",
        "lowestPrice",
        "priceCapacityOptimized",
        "prioritized",
    }
)

# Maps every accepted input variant to its canonical camelCase form.
_ALLOCATION_STRATEGY_NORMALISATION_MAP: dict[str, str] = {
    # camelCase (HF / SpotFleet wire format) — identity mappings
    "capacityOptimized": "capacityOptimized",
    "capacityOptimizedPrioritized": "capacityOptimizedPrioritized",
    "diversified": "diversified",
    "lowestPrice": "lowestPrice",
    "priceCapacityOptimized": "priceCapacityOptimized",
    "prioritized": "prioritized",
    # hyphenated (EC2Fleet / ASG API format)
    "capacity-optimized": "capacityOptimized",
    "capacity-optimized-prioritized": "capacityOptimizedPrioritized",
    "lowest-price": "lowestPrice",
    "price-capacity-optimized": "priceCapacityOptimized",
    # snake_case (legacy domain enum values)
    "capacity_optimized": "capacityOptimized",
    "capacity_optimized_prioritized": "capacityOptimizedPrioritized",
    "lowest_price": "lowestPrice",
    "price_capacity_optimized": "priceCapacityOptimized",
}


def normalise_allocation_strategy(value: str) -> str:
    """Return the canonical camelCase form of an allocation strategy string.

    Accepts any of the three formats used across the AWS provider:
    - camelCase (HF/SpotFleet wire format): ``capacityOptimized``, ``lowestPrice``, …
    - hyphenated (EC2Fleet/ASG API format): ``capacity-optimized``, ``lowest-price``, …
    - snake_case (legacy domain enum): ``capacity_optimized``, ``lowest_price``, …

    Returns the canonical camelCase string, which is the form stored on disk in
    ``aws_templates.json`` and used by the HF scheduler.

    Raises:
        ValueError: if *value* does not match any known allocation strategy.
    """
    canonical = _ALLOCATION_STRATEGY_NORMALISATION_MAP.get(value)
    if canonical is None:
        valid = ", ".join(sorted(CANONICAL_ALLOCATION_STRATEGIES))
        raise ValueError(
            f"Unknown allocation strategy {value!r}. Valid canonical values are: {valid}"
        )
    return canonical


class AWSConfiguration(ValueObject):
    """AWS-specific configuration value object - clean domain object without infrastructure dependencies."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    handler_type: ProviderApi
    fleet_type: Optional[AWSFleetType] = None
    allocation_strategy: Optional[str] = None
    price_type: Optional[PriceType] = None
    subnet_ids: list[AWSSubnetId] = []
    security_group_ids: list[AWSSecurityGroupId] = []

    @model_validator(mode="after")
    def validate_aws_configuration(self) -> "AWSConfiguration":
        """Validate AWS-specific configuration - basic domain validation only."""
        # Set default fleet type if not provided
        if not self.fleet_type:
            # Use simple default without configuration dependency
            object.__setattr__(self, "fleet_type", AWSFleetType.REQUEST)

        # Set default allocation strategy if not provided
        if not self.allocation_strategy:
            object.__setattr__(self, "allocation_strategy", "lowestPrice")

        # Set default price type if not provided
        if not self.price_type:
            object.__setattr__(self, "price_type", PriceType.ONDEMAND)

        return self

    def to_aws_api_format(self) -> dict[str, Any]:
        """Convert to AWS API format."""
        return {
            "handler_type": self.handler_type.value,
            "fleet_type": self.fleet_type.value if self.fleet_type else None,
            "allocation_strategy": self.allocation_strategy,
            "price_type": self.price_type.value if self.price_type else None,
            "subnet_ids": [subnet.value for subnet in self.subnet_ids],
            "security_group_ids": [sg.value for sg in self.security_group_ids],
        }
