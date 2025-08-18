"""AWS-specific template extension configuration."""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class AMIResolutionConfig(BaseModel):
    """AMI resolution configuration for AWS templates."""

    enabled: bool = Field(True, description="Enable AMI resolution from SSM parameters")
    fallback_on_failure: bool = Field(True, description="Return SSM parameter if resolution fails")
    ssm_parameter_prefix: str = Field(
        "/hostfactory/templates/", description="SSM parameter prefix for templates"
    )


class AWSTemplateExtensionConfig(BaseModel):
    """AWS-specific template extension configuration.

    This contains all AWS-specific template configuration that was previously
    mixed into the generic TemplateConfig. These extensions are applied to
    AWS templates through the hierarchical defaults system.
    """

    # AMI resolution configuration
    ami_resolution: AMIResolutionConfig = Field(
        default_factory=AMIResolutionConfig, description="AMI resolution configuration"
    )

    # AWS instance configuration defaults
    fleet_type: Optional[str] = Field(None, description="Fleet type for EC2/Spot Fleet handlers")
    fleet_role: Optional[str] = Field(None, description="IAM role for Spot Fleet")
    key_name: Optional[str] = Field(None, description="Default key name")
    user_data_script: Optional[str] = Field(None, description="User data script")
    instance_profile: Optional[str] = Field(None, description="Instance profile")

    # AWS pricing and allocation defaults
    max_spot_price: Optional[float] = Field(None, description="Maximum price for Spot instances")
    spot_fleet_request_expiry: int = Field(
        30, description="Time before unfulfilled requests are canceled (minutes)"
    )
    allocation_strategy: str = Field("capacityOptimized", description="Strategy for Spot instances")
    allocation_strategy_on_demand: str = Field(
        "lowestPrice", description="Strategy for On-Demand instances"
    )
    percent_on_demand: int = Field(
        0, description="Percentage of On-Demand capacity in heterogeneous"
    )
    pools_count: Optional[int] = Field(None, description="Number of Spot instance pools to use")

    # AWS storage configuration defaults
    volume_type: str = Field("gp3", description="Type of EBS volume")
    iops: Optional[int] = Field(None, description="I/O operations per second for io1/io2 volumes")
    root_device_volume_size: Optional[int] = Field(
        None, description="Size of EBS root volume in GiB"
    )

    # AWS instance type configuration
    vm_type: Optional[str] = Field(None, description="EC2 instance type for ondemand")
    vm_types: Optional[Dict[str, int]] = Field(
        None, description="Map of instance types and weights for spot/heterogeneous"
    )
    vm_types_on_demand: Optional[Dict[str, int]] = Field(
        None, description="On-Demand instance types for heterogeneous"
    )
    vm_types_priority: Optional[Dict[str, int]] = Field(
        None, description="Priority settings for instance types"
    )

    # AWS network configuration defaults (moved from deprecated template schema fields)
    subnet_ids: Optional[List[str]] = Field(None, description="Default subnet IDs")
    security_group_ids: Optional[List[str]] = Field(None, description="Default security group IDs")

    # AWS Context field for fleet operations
    context: Optional[str] = Field(
        None, description="AWS Context field for EC2 Fleet, ASG, and Spot Fleet"
    )

    @field_validator("subnet_ids")
    @classmethod
    def validate_subnet_ids(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Validate subnet IDs."""
        if v is not None and not v:
            raise ValueError("If subnet_ids is provided, at least one subnet ID is required") from e
        return v

    @field_validator("security_group_ids")
    @classmethod
    def validate_security_group_ids(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Validate security group IDs."""
        if v is not None and not v:
            raise ValueError(
                "If security_group_ids is provided, at least one security group ID is required"
            )
        return v

    @field_validator("spot_fleet_request_expiry")
    @classmethod
    def validate_spot_fleet_request_expiry(cls, v: int) -> int:
        """Validate spot fleet request expiry."""
        if v <= 0:
            raise ValueError("Spot fleet request expiry must be positive") from e
        return v

    @field_validator("percent_on_demand")
    @classmethod
    def validate_percent_on_demand(cls, v: int) -> int:
        """Validate percent on demand."""
        if not (0 <= v <= 100):
            raise ValueError("Percent on demand must be between 0 and 100") from e
        return v

    def to_template_defaults(self) -> Dict[str, any]:
        """Convert extension config to template defaults format.

        This method converts the extension configuration to the format
        expected by the template defaults system, with clean field names
        (no default_ prefixes).
        """
        defaults = {}

        # Add non-None values to defaults
        for field_name, field_value in self.model_dump().items():
            if field_value is not None:
                # Handle nested AMI resolution config
                if field_name == "ami_resolution" and isinstance(field_value, dict):
                    defaults.update(field_value)
                else:
                    defaults[field_name] = field_value

        return defaults
