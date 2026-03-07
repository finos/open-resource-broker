"""Template DTOs for infrastructure layer - avoiding direct domain aggregate imports."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from pydantic import Field, model_validator

from orb.application.dto.base import BaseDTO


class TemplateDTO(BaseDTO):
    """
    Template Data Transfer Object for infrastructure layer.

    Follows DIP by providing infrastructure representation without
    depending on domain aggregates directly.
    """

    # Core template fields
    template_id: str
    name: Optional[str] = None
    description: Optional[str] = None

    # Instance configuration
    image_id: Optional[str] = None
    max_instances: int = 1

    # Machine types configuration (unified)
    machine_types: dict[str, int] = Field(default_factory=dict)
    machine_types_ondemand: dict[str, int] = Field(default_factory=dict)
    machine_types_priority: dict[str, int] = Field(default_factory=dict)

    # Network configuration
    subnet_ids: list[str] = Field(default_factory=list)
    security_group_ids: list[str] = Field(default_factory=list)

    # Pricing and allocation
    price_type: str = "ondemand"
    allocation_strategy: Optional[str] = None
    max_price: Optional[float] = None

    # Network configuration
    network_zones: list[str] = Field(default_factory=list)
    public_ip_assignment: Optional[bool] = None

    # Storage configuration
    root_device_volume_size: Optional[int] = None
    volume_type: Optional[str] = None
    iops: Optional[int] = None
    throughput: Optional[int] = None
    storage_encryption: Optional[bool] = None
    encryption_key: Optional[str] = None

    # Access and security
    key_name: Optional[str] = None
    user_data: Optional[str] = None
    instance_profile: Optional[str] = None

    # Advanced configuration
    monitoring_enabled: Optional[bool] = None

    # Tags and metadata
    tags: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Provider configuration
    provider_type: Optional[str] = None
    provider_name: Optional[str] = None
    provider_api: Optional[str] = None

    # Timestamps
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Active status
    is_active: bool = True

    # Legacy fields
    version: Optional[str] = None

    # AWS-specific fields
    fleet_role: Optional[str] = None
    fleet_type: Optional[str] = None
    percent_on_demand: Optional[int] = None
    abis_instance_requirements: Optional[dict[str, Any]] = None

    @model_validator(mode="before")
    @classmethod
    def _set_defaults(cls, data: Any) -> Any:
        """Set default values for optional fields derived from other fields."""
        if isinstance(data, dict):
            if not data.get("name"):
                data["name"] = data.get("template_id")
        return data

    @classmethod
    def from_domain(cls, template) -> "TemplateDTO":
        """Convert domain template to DTO."""
        _fleet_type = getattr(template, "fleet_type", None)
        _fleet_type_str: Optional[str] = (
            str(_fleet_type.value)
            if _fleet_type is not None and hasattr(_fleet_type, "value")
            else (_fleet_type if _fleet_type is None else str(_fleet_type))
        )
        _abis = getattr(template, "abis_instance_requirements", None)
        return cls(
            # Core fields
            template_id=template.template_id,
            name=getattr(template, "name", None),
            description=getattr(template, "description", None),
            # Instance configuration
            image_id=getattr(template, "image_id", None),
            max_instances=getattr(template, "max_instances", 1),
            # Machine types configuration (unified)
            machine_types=getattr(template, "machine_types", {}),
            machine_types_ondemand=getattr(template, "machine_types_ondemand", {}),
            machine_types_priority=getattr(template, "machine_types_priority", {}),
            # Network configuration
            subnet_ids=getattr(template, "subnet_ids", []),
            security_group_ids=getattr(template, "security_group_ids", []),
            # Pricing and allocation
            price_type=getattr(template, "price_type", "ondemand"),
            allocation_strategy=getattr(template, "allocation_strategy", None),
            max_price=getattr(template, "max_price", None),
            # Network configuration
            network_zones=getattr(template, "network_zones", []),
            public_ip_assignment=getattr(template, "public_ip_assignment", None),
            # Storage configuration
            root_device_volume_size=getattr(template, "root_device_volume_size", None),
            volume_type=getattr(template, "volume_type", None),
            iops=getattr(template, "iops", None),
            throughput=getattr(template, "throughput", None),
            storage_encryption=getattr(template, "storage_encryption", None),
            encryption_key=getattr(template, "encryption_key", None),
            # Access and security
            key_name=getattr(template, "key_name", None),
            user_data=getattr(template, "user_data", None),
            instance_profile=getattr(template, "instance_profile", None),
            # Advanced configuration
            monitoring_enabled=getattr(template, "monitoring_enabled", None),
            # Tags and metadata
            tags=getattr(template, "tags", {}),
            metadata=getattr(template, "metadata", {}),
            # Provider configuration
            provider_type=getattr(template, "provider_type", None),
            provider_name=getattr(template, "provider_name", None),
            provider_api=getattr(template, "provider_api", None),
            # Timestamps
            created_at=getattr(template, "created_at", None),
            updated_at=getattr(template, "updated_at", None),
            # Active status
            is_active=getattr(template, "is_active", True),
            # Legacy fields
            version=getattr(template, "version", None),
            # AWS-specific fields
            fleet_role=getattr(template, "fleet_role", None),
            fleet_type=_fleet_type_str,
            percent_on_demand=getattr(template, "percent_on_demand", None),
            abis_instance_requirements=_abis.to_aws_dict() if _abis is not None else None,
        )


@dataclass
class TemplateValidationResultDTO:
    """Template validation result DTO."""

    is_valid: bool
    errors: list[str]
    warnings: list[str]
    template_id: str

    def has_errors(self) -> bool:
        """Check if validation has errors."""
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        """Check if validation has warnings."""
        return len(self.warnings) > 0


@dataclass
class TemplateCacheEntryDTO:
    """Template cache entry DTO."""

    template: TemplateDTO
    cached_at: datetime
    expires_at: Optional[datetime] = None
    access_count: int = 0

    def is_expired(self) -> bool:
        """Check if cache entry is expired."""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at
