"""AWS configuration validation and naming patterns."""

from dataclasses import dataclass, field
from typing import Optional

# Import AWSProviderConfig for compatibility
from .config import AWSProviderConfig
from .naming_config import AWSNamingConfig


@dataclass
class AWSLimits:
    """AWS service limits and constraints."""

    tag_key_length: int = 128
    tag_value_length: int = 256
    max_tags_per_resource: int = 50
    max_security_groups: int = 5
    max_subnets: int = 16


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

    types: dict[str, str] = field(
        default_factory=lambda: {
            "ec2_fleet": "EC2Fleet",
            "spot_fleet": "SpotFleet",
            "asg": "ASG",
            "run_instances": "RunInstances",
        }
    )

    capabilities: dict[str, AWSHandlerCapabilities] = field(
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


class AWSConfigManager:
    """Manager for AWS configuration."""

    def __init__(self, provider_config: Optional[AWSProviderConfig] = None) -> None:
        """Initialize with an optional provider config to load naming patterns from."""
        self._provider_config = provider_config

    @property
    def _naming_config(self) -> AWSNamingConfig:
        """Return naming config from provider config if available, else defaults."""
        if self._provider_config is not None:
            return self._provider_config.naming
        return AWSNamingConfig()  # type: ignore[call-arg]

    def configure(self, provider_config: AWSProviderConfig) -> None:
        """Update the provider config used to resolve naming patterns."""
        self._provider_config = provider_config

    def get_typed(self, config_type):
        """Get typed configuration."""
        if config_type == AWSNamingConfig:
            return self._naming_config
        if config_type == AWSProviderConfig:
            if self._provider_config is not None:
                return self._provider_config
            raise ValueError(
                "AWSProviderConfig not available from config manager — no AWS provider configured"
            )
        raise ValueError(f"Unknown AWS config type: {config_type}")


# Global AWS config manager instance — can be reconfigured at startup via configure()
_aws_config_manager = AWSConfigManager()


def get_aws_config_manager() -> AWSConfigManager:
    """Get the global AWS configuration manager."""
    return _aws_config_manager


__all__: list[str] = [
    "AWSConfigManager",
    "AWSLimits",
    "AWSNamingConfig",
    "AWSProviderConfig",
    "get_aws_config_manager",
]
