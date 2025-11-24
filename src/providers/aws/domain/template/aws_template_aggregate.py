"""AWS-specific template domain extensions."""

from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from domain.template.template_aggregate import Template
from providers.aws.domain.template.value_objects import (
    AWSAllocationStrategy,
    AWSConfiguration,
    AWSFleetType,
    AWSInstanceType,
    AWSSecurityGroupId,
    AWSSubnetId,
    AWSTags,
    ProviderApi,
)


class AWSOptionalIntegerRange(BaseModel):
    """Optional integer range used by AWS instance requirements."""

    model_config = ConfigDict(populate_by_name=True)

    min: Optional[int] = Field(
        default=None,
        alias="Min",
        validation_alias=AliasChoices("Min", "min"),
    )
    max: Optional[int] = Field(
        default=None,
        alias="Max",
        validation_alias=AliasChoices("Max", "max"),
    )


class AWSRequiredIntegerRange(AWSOptionalIntegerRange):
    """Required integer range used by AWS instance requirements."""

    min: int = Field(alias="Min", validation_alias=AliasChoices("Min", "min"))
    max: int = Field(alias="Max", validation_alias=AliasChoices("Max", "max"))


class AWSOptionalFloatRange(BaseModel):
    """Optional float range used by AWS instance requirements."""

    model_config = ConfigDict(populate_by_name=True)

    min: Optional[float] = Field(
        default=None,
        alias="Min",
        validation_alias=AliasChoices("Min", "min"),
    )
    max: Optional[float] = Field(
        default=None,
        alias="Max",
        validation_alias=AliasChoices("Max", "max"),
    )


class ABISInstanceRequirements(BaseModel):
    """Attribute-based instance selection requirements for AWS fleets."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    vcpu_count: AWSRequiredIntegerRange = Field(
        alias="VCpuCount",
        validation_alias=AliasChoices("VCpuCount", "vcpu_count"),
    )
    memory_mib: AWSRequiredIntegerRange = Field(
        alias="MemoryMiB",
        validation_alias=AliasChoices("MemoryMiB", "memory_mib"),
    )

    cpu_manufacturers: Optional[list[str]] = Field(
        default=None,
        alias="CpuManufacturers",
        validation_alias=AliasChoices("CpuManufacturers", "cpu_manufacturers"),
    )
    memory_gib_per_vcpu: Optional[AWSOptionalFloatRange] = Field(
        default=None,
        alias="MemoryGiBPerVCpu",
        validation_alias=AliasChoices("MemoryGiBPerVCpu", "memory_gib_per_vcpu"),
    )
    excluded_instance_types: Optional[list[str]] = Field(
        default=None,
        alias="ExcludedInstanceTypes",
        validation_alias=AliasChoices("ExcludedInstanceTypes", "excluded_instance_types"),
    )
    instance_generations: Optional[list[str]] = Field(
        default=None,
        alias="InstanceGenerations",
        validation_alias=AliasChoices("InstanceGenerations", "instance_generations"),
    )
    spot_max_price_percentage_over_lowest_price: Optional[int] = Field(
        default=None,
        alias="SpotMaxPricePercentageOverLowestPrice",
        validation_alias=AliasChoices(
            "SpotMaxPricePercentageOverLowestPrice",
            "spot_max_price_percentage_over_lowest_price",
        ),
    )
    on_demand_max_price_percentage_over_lowest_price: Optional[int] = Field(
        default=None,
        alias="OnDemandMaxPricePercentageOverLowestPrice",
        validation_alias=AliasChoices(
            "OnDemandMaxPricePercentageOverLowestPrice",
            "on_demand_max_price_percentage_over_lowest_price",
        ),
    )
    bare_metal: Optional[str] = Field(
        default=None,
        alias="BareMetal",
        validation_alias=AliasChoices("BareMetal", "bare_metal"),
    )
    burstable_performance: Optional[str] = Field(
        default=None,
        alias="BurstablePerformance",
        validation_alias=AliasChoices("BurstablePerformance", "burstable_performance"),
    )
    require_hibernate_support: Optional[bool] = Field(
        default=None,
        alias="RequireHibernateSupport",
        validation_alias=AliasChoices("RequireHibernateSupport", "require_hibernate_support"),
    )

    network_interface_count: Optional[AWSOptionalIntegerRange] = Field(
        default=None,
        alias="NetworkInterfaceCount",
        validation_alias=AliasChoices("NetworkInterfaceCount", "network_interface_count"),
    )
    local_storage: Optional[str] = Field(
        default=None,
        alias="LocalStorage",
        validation_alias=AliasChoices("LocalStorage", "local_storage"),
    )
    local_storage_types: Optional[list[str]] = Field(
        default=None,
        alias="LocalStorageTypes",
        validation_alias=AliasChoices("LocalStorageTypes", "local_storage_types"),
    )
    total_local_storage_gb: Optional[AWSOptionalFloatRange] = Field(
        default=None,
        alias="TotalLocalStorageGB",
        validation_alias=AliasChoices("TotalLocalStorageGB", "total_local_storage_gb"),
    )
    baseline_ebs_bandwidth_mbps: Optional[AWSOptionalIntegerRange] = Field(
        default=None,
        alias="BaselineEbsBandwidthMbps",
        validation_alias=AliasChoices("BaselineEbsBandwidthMbps", "baseline_ebs_bandwidth_mbps"),
    )

    accelerator_types: Optional[list[str]] = Field(
        default=None,
        alias="AcceleratorTypes",
        validation_alias=AliasChoices("AcceleratorTypes", "accelerator_types"),
    )
    accelerator_count: Optional[AWSOptionalIntegerRange] = Field(
        default=None,
        alias="AcceleratorCount",
        validation_alias=AliasChoices("AcceleratorCount", "accelerator_count"),
    )
    accelerator_manufacturers: Optional[list[str]] = Field(
        default=None,
        alias="AcceleratorManufacturers",
        validation_alias=AliasChoices("AcceleratorManufacturers", "accelerator_manufacturers"),
    )
    accelerator_names: Optional[list[str]] = Field(
        default=None,
        alias="AcceleratorNames",
        validation_alias=AliasChoices("AcceleratorNames", "accelerator_names"),
    )
    accelerator_total_memory_mib: Optional[AWSOptionalIntegerRange] = Field(
        default=None,
        alias="AcceleratorTotalMemoryMiB",
        validation_alias=AliasChoices(
            "AcceleratorTotalMemoryMiB",
            "accelerator_total_memory_mib",
        ),
    )
    allowed_instance_types: Optional[list[str]] = Field(
        default=None,
        alias="AllowedInstanceTypes",
        validation_alias=AliasChoices("AllowedInstanceTypes", "allowed_instance_types"),
    )

    def to_aws_dict(self) -> dict[str, Any]:
        """Serialize instance requirements to AWS API format."""
        return self.model_dump(by_alias=True, exclude_none=True)


class AWSTemplate(Template):
    """AWS-specific template with AWS extensions."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # AWS-specific fields
    provider_api: ProviderApi
    fleet_type: Optional[AWSFleetType] = None
    fleet_role: Optional[str] = None
    key_name: Optional[str] = None
    user_data: Optional[str] = None

    # AWS instance configuration
    root_device_volume_size: Optional[int] = None
    volume_type: Optional[str] = "gp3"  # gp2, gp3, io1, io2, standard
    iops: Optional[int] = None
    instance_profile: Optional[str] = None

    # AWS spot configuration
    spot_fleet_request_expiry: Optional[int] = None
    allocation_strategy_on_demand: Optional[AWSAllocationStrategy] = None
    percent_on_demand: Optional[int] = None
    pools_count: Optional[int] = None

    # AWS launch template
    launch_template_id: Optional[str] = None
    launch_template_version: Optional[str] = None

    # AWS-specific instance types and priorities (extends Template.instance_types)
    instance_types_ondemand: Optional[dict[str, int]] = None
    instance_types_priority: Optional[dict[str, int]] = None

    # Native spec fields (flattened, no nesting)
    launch_template_spec: Optional[dict[str, Any]] = None
    launch_template_spec_file: Optional[str] = None
    provider_api_spec: Optional[dict[str, Any]] = None
    provider_api_spec_file: Optional[str] = None

    # Attribute-based instance selection (InstanceRequirements payload)
    abis_instance_requirements: Optional[ABISInstanceRequirements] = Field(
        default=None,
        alias="abis_instance_requirements",
        validation_alias=AliasChoices("abis_instance_requirements", "abisInstanceRequirements"),
    )

    # AWS Context field for fleet operations
    context: Optional[str] = None

    # Note: instance_type and instance_types are inherited from CoreTemplate
    # No need to redefine them here - this was causing the field access issues

    def __init__(self, **data) -> None:
        """Initialize the instance."""
        # Set provider_type to AWS
        data["provider_type"] = "aws"
        super().__init__(**data)

    @model_validator(mode="after")
    def validate_aws_template(self) -> "AWSTemplate":
        """AWS-specific template validation."""
        # AWS-specific required fields
        if not self.image_id:
            raise ValueError("image_id is required for AWS templates")

        if not self.subnet_ids:
            raise ValueError("At least one subnet_id is required for AWS templates")

        # Auto-assign default fleet_type if not provided
        # Set fleet_type from metadata if not already set
        if not self.fleet_type:
            # Check both metadata.fleet_type and metadata.providerConfig.fleet_type
            metadata = self.metadata or {}
            fleet_type_value = metadata.get("fleet_type") or metadata.get("providerConfig", {}).get(
                "fleet_type"
            )
            if fleet_type_value:
                try:
                    normalized_value = str(fleet_type_value).strip().lower()
                    if normalized_value:
                        object.__setattr__(self, "fleet_type", AWSFleetType(normalized_value))
                except (ValueError, TypeError):
                    pass  # Let defaulting logic handle invalid values
        if (
            not self.fleet_type
            and self.provider_api
            and self.provider_api in [ProviderApi.EC2_FLEET, ProviderApi.SPOT_FLEET]
        ):
            # Use simple default without configuration dependency
            object.__setattr__(self, "fleet_type", AWSFleetType.REQUEST)

        # Validate spot configuration
        if self.percent_on_demand is not None and not (0 <= self.percent_on_demand <= 100):
            raise ValueError("percent_on_demand must be between 0 and 100")

        # Validate launch template version format
        if self.launch_template_version is not None:
            version = str(self.launch_template_version)
            if version not in ["$Latest", "$Default"]:
                # Must be a positive integer
                try:
                    version_int = int(version)
                    if version_int < 1:
                        raise ValueError(
                            "launch_template_version must be a positive integer, '$Latest', or '$Default'"
                        )
                except ValueError:
                    raise ValueError(
                        "launch_template_version must be a positive integer, '$Latest', or '$Default'"
                    )

        return self

    def get_ec2_fleet_allocation_strategy(self) -> str:
        """Get allocation strategy in EC2 Fleet API format."""
        if isinstance(self.allocation_strategy, AWSAllocationStrategy):
            return self.allocation_strategy.to_ec2_fleet_format()
        return AWSAllocationStrategy.LOWEST_PRICE.to_ec2_fleet_format()

    def get_spot_fleet_allocation_strategy(self) -> str:
        """Get allocation strategy in Spot Fleet API format."""
        if isinstance(self.allocation_strategy, AWSAllocationStrategy):
            return self.allocation_strategy.to_spot_fleet_format()
        return AWSAllocationStrategy.LOWEST_PRICE.to_spot_fleet_format()

    def get_asg_allocation_strategy(self) -> str:
        """Get allocation strategy in Auto Scaling Group API format."""
        if isinstance(self.allocation_strategy, AWSAllocationStrategy):
            return self.allocation_strategy.to_asg_format()
        return AWSAllocationStrategy.LOWEST_PRICE.to_asg_format()

    def get_ec2_fleet_on_demand_allocation_strategy(self) -> str:
        """Get on-demand allocation strategy in EC2 Fleet API format."""
        if self.allocation_strategy_on_demand:
            return self.allocation_strategy_on_demand.to_ec2_fleet_format()
        return self.get_ec2_fleet_allocation_strategy()

    def to_aws_api_format(self) -> dict[str, Any]:
        """Convert template to AWS API format."""
        base_format = self.to_provider_format("aws")

        # Add AWS-specific fields
        aws_format = {
            **base_format,
            "provider_api": self.provider_api.value,
            "fleet_type": self.fleet_type.value if self.fleet_type else None,
            "fleet_role": self.fleet_role,
            "key_name": self.key_name,
            "user_data": self.user_data,
            "root_device_volume_size": self.root_device_volume_size,
            "volume_type": self.volume_type,
            "iops": self.iops,
            "instance_profile": self.instance_profile,
            "spot_fleet_request_expiry": self.spot_fleet_request_expiry,
            "percent_on_demand": self.percent_on_demand,
            "pools_count": self.pools_count,
            "launch_template_id": self.launch_template_id,
            "launch_template_version": self.launch_template_version,
            "instance_types_ondemand": self.instance_types_ondemand,
            "instance_types_priority": self.instance_types_priority,
        }

        # Add AWS-specific allocation strategies
        if self.allocation_strategy_on_demand:
            aws_format["allocation_strategy_on_demand"] = self.allocation_strategy_on_demand.value

        return aws_format

    @classmethod
    def from_aws_format(cls, data: dict[str, Any]) -> "AWSTemplate":
        """Create AWS template from AWS-specific format."""
        # Convert AWS format to core format first
        core_data = {
            "template_id": data.get("template_id"),
            "name": data.get("name", data.get("template_id")),
            "instance_type": AWSInstanceType(value=data.get("vm_type", data.get("instance_type"))),
            "image_id": data.get("image_id"),
            "max_instances": data.get("max_number", data.get("max_instances", 1)),
            "subnet_ids": data.get(
                "subnet_ids", [data.get("subnet_id")] if data.get("subnet_id") else []
            ),
            "security_group_ids": data.get("security_group_ids", []),
            "tags": AWSTags.from_dict(data.get("tags", data.get("instance_tags", {}))),
        }

        # Support multi-instance mappings (vmTypes/instance_types) when provided
        instance_types_map = (
            data.get("instance_types") or data.get("vm_types") or data.get("vmTypes")
        )
        if instance_types_map:
            core_data["instance_types"] = instance_types_map

        # Add AWS-specific fields
        aws_data = {
            **core_data,
            "provider_api": ProviderApi(data.get("provider_api")),
            "fleet_type": (
                AWSFleetType(data.get("fleet_type")) if data.get("fleet_type") else None
            ),
            "fleet_role": data.get("fleet_role"),
            "key_name": data.get("key_name"),
            "user_data": data.get("user_data"),
            "root_device_volume_size": data.get("root_device_volume_size")
            or data.get("rootDeviceVolumeSize"),
            "volume_type": data.get("volume_type") or data.get("volumeType"),
            "iops": data.get("iops"),
            "instance_profile": data.get("instance_profile"),
            "spot_fleet_request_expiry": data.get("spot_fleet_request_expiry"),
            "percent_on_demand": data.get("percent_on_demand"),
            "pools_count": data.get("pools_count"),
            "launch_template_id": data.get("launch_template_id"),
            "launch_template_version": data.get("launch_template_version"),
            "instance_types_ondemand": data.get("instance_types_ondemand"),
            "instance_types_priority": data.get("instance_types_priority"),
        }

        # Handle optional AWS-specific fields
        if "allocation_strategy" in data:
            aws_data["allocation_strategy"] = AWSAllocationStrategy.from_string(
                data["allocation_strategy"]
            )

        if "allocation_strategy_on_demand" in data:
            aws_data["allocation_strategy_on_demand"] = AWSAllocationStrategy.from_string(
                data["allocation_strategy_on_demand"]
            )

        if "price_type" in data:
            from domain.base.value_objects import PriceType

            aws_data["price_type"] = PriceType.from_string(data["price_type"])

        if "max_spot_price" in data:
            aws_data["max_price"] = data["max_spot_price"]

        instance_requirements = data.get("abis_instance_requirements") or data.get(
            "abisInstanceRequirements"
        )
        if instance_requirements:
            aws_data["abis_instance_requirements"] = instance_requirements

        return cls.model_validate(aws_data)

    @model_validator(mode="after")
    def validate_native_spec_mutual_exclusion(self) -> "AWSTemplate":
        """Validate mutual exclusion of spec and spec_file fields."""
        if self.launch_template_spec and self.launch_template_spec_file:
            raise ValueError(
                "Cannot specify both launch_template_spec and launch_template_spec_file"
            )
        if self.provider_api_spec and self.provider_api_spec_file:
            raise ValueError("Cannot specify both provider_api_spec and provider_api_spec_file")
        return self

    def get_aws_configuration(self) -> AWSConfiguration:
        """Get AWS configuration object."""
        return AWSConfiguration(
            handler_type=self.provider_api,
            fleet_type=self.fleet_type,
            allocation_strategy=(
                self.allocation_strategy
                if isinstance(self.allocation_strategy, AWSAllocationStrategy)
                else None
            ),
            price_type=self.price_type,
            subnet_ids=[AWSSubnetId(value=sid) for sid in self.subnet_ids],
            security_group_ids=[AWSSecurityGroupId(value=sgid) for sgid in self.security_group_ids],
        )

    def get_instance_requirements_payload(self) -> Optional[dict[str, Any]]:
        """Return the InstanceRequirements payload for AWS APIs if provided."""
        if self.abis_instance_requirements:
            return self.abis_instance_requirements.to_aws_dict()
        return None
