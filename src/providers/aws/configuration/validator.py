"""AWS configuration validation and naming patterns."""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class AWSLimits:
    """AWS service limits and constraints."""

    tag_key_length: int = 128
    tag_value_length: int = 256
    max_tags_per_resource: int = 50
    max_security_groups: int = 5
    max_subnets: int = 16


@dataclass
class AWSNamingConfig:
    """AWS naming patterns and validation rules."""

    patterns: Dict[str, str] = field(
        default_factory=lambda: {
            "subnet": r"^subnet-[0-9a-f]{8,17}$",
            "security_group": r"^sg-[0-9a-f]{8,17}$",
            "ec2_instance": r"^i-[0-9a-f]{8,17}$",
            "ami": r"^ami-[0-9a-f]{8,17}$",
            "ec2_fleet": r"^fleet-[0-9a-f]{8,17}$",
            "launch_template": r"^lt-[0-9a-f]{8,17}$",
            "instance_type": r"^[a-z][0-9]+[a-z]*\.[a-z0-9]+$",
            "tag_key": r"^[a-zA-Z0-9\s\._:/=+\-@]{1,128}$",
            "arn": r"^arn:aws:[a-zA-Z0-9\-]+:[a-zA-Z0-9\-]*:[0-9]{12}:.+$",
        }
    )

    limits: AWSLimits = field(default_factory=AWSLimits)


@dataclass
class AWSHandlerCapabilities:
    """AWS handler capabilities and defaults."""

    supported_fleet_types: Optional[list] = None
    default_fleet_type: Optional[str] = None
    supports_spot: bool = True
    supports_on_demand: bool = True


@dataclass
class AWSHandlerDefaults:
    """AWS handler default values."""

    ec2_fleet_type: str = "request"
    spot_fleet_type: str = "request"
    allocation_strategy: str = "lowest_price"
    price_type: str = "ondemand"


@dataclass
class AWSHandlerConfig:
    """AWS handler configuration."""

    types: Dict[str, str] = field(
        default_factory=lambda: {
            "ec2_fleet": "EC2Fleet",
            "spot_fleet": "SpotFleet",
            "asg": "ASG",
            "run_instances": "RunInstances",
        }
    )

    capabilities: Dict[str, AWSHandlerCapabilities] = field(
        default_factory=lambda: {
            "EC2Fleet": AWSHandlerCapabilities(
                supported_fleet_types=["instant", "request", "maintain"],
                default_fleet_type="request",
                supports_spot=True,
                supports_on_demand=True,
            ),
            "SpotFleet": AWSHandlerCapabilities(
                supported_fleet_types=["request", "maintain"],
                default_fleet_type="request",
                supports_spot=True,
                supports_on_demand=False,
            ),
            "ASG": AWSHandlerCapabilities(
                supported_fleet_types=[],
                default_fleet_type=None,
                supports_spot=True,
                supports_on_demand=True,
            ),
            "RunInstances": AWSHandlerCapabilities(
                supported_fleet_types=[],
                default_fleet_type=None,
                supports_spot=False,
                supports_on_demand=True,
            ),
        }
    )

    defaults: AWSHandlerDefaults = field(default_factory=AWSHandlerDefaults)


# Global AWS configuration instances
_aws_naming_config = AWSNamingConfig()


class AWSConfigManager:
    """Manager for AWS configuration."""

    def __init__(self):
        """Initialize the instance."""
        self._naming_config = _aws_naming_config

    def get_typed(self, config_type):
        """Get typed configuration."""
        if config_type == AWSNamingConfig:
            return self._naming_config
        else:
            # Import here to avoid circular imports
            from .config import AWSProviderConfig

            if config_type == AWSProviderConfig:
                # Return a default instance - in real usage this would be injected
                return AWSProviderConfig()
            raise ValueError(f"Unknown AWS config type: {config_type}")


# Global AWS config manager instance
_aws_config_manager = AWSConfigManager()


def get_aws_config_manager() -> AWSConfigManager:
    """Get the global AWS configuration manager."""
    return _aws_config_manager


# Import AWSProviderConfig for compatibility
from .config import AWSProviderConfig

__all__ = ["AWSLimits", "AWSNamingConfig", "AWSConfigManager", "get_aws_config_manager", "AWSProviderConfig"]
