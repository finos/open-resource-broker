"""Template DTOs for infrastructure layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_serializer, model_validator

from orb.application.dto.base import BaseDTO
from orb.domain.template.extensions import TemplateExtensionRegistry
from orb.domain.template.template_aggregate import Template as DomainTemplate


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
    instance_type: Optional[str] = None
    vm_size: Optional[str] = None
    vm_sizes: list[str] = Field(default_factory=list)
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
    placement_split_strategy: str = "hybrid"
    placement_primary_share_percent: int = 80
    placement_regions: list[str] = Field(default_factory=list)
    placement_zones: list[str] = Field(default_factory=list)

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
    launch_template_id: Optional[str] = None

    # Advanced configuration
    monitoring_enabled: Optional[bool] = None

    # Tags and metadata
    tags: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    provider_config: Optional[BaseModel] = None

    # Provider-specific data (keyed by provider name, e.g. {"aws": {...}})
    provider_data: dict[str, Any] = Field(default_factory=dict)

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

    @field_serializer("provider_config")
    def _serialize_provider_config(self, value: Optional[BaseModel]) -> Optional[dict[str, Any]]:
        if value is None:
            return None
        return value.model_dump(exclude_none=True, exclude_unset=True)

    @model_validator(mode="before")
    @classmethod
    def _set_defaults(cls, data: Any) -> Any:
        """Set default values for optional fields derived from other fields."""
        if isinstance(data, dict):
            data = dict(data)
            if not data.get("name"):
                data["name"] = data.get("template_id")
            provider_config = data.get("provider_config")
            provider_type = data.get("provider_type")
            if isinstance(provider_config, dict):
                typed_provider_config = (
                    TemplateExtensionRegistry.create_extension_config(
                        str(provider_type), provider_config
                    )
                    if provider_type
                    else None
                )
                if typed_provider_config is None:
                    raise ValueError(
                        "provider_config requires a registered provider template extension"
                    )
                data["provider_config"] = typed_provider_config
        return data

    @model_validator(mode="after")
    def _validate_provider_config_registration(self) -> TemplateDTO:
        if self.provider_config is None:
            return self

        provider_type = str(self.provider_type) if self.provider_type else None
        extension_class = (
            TemplateExtensionRegistry.get_extension_class(provider_type) if provider_type else None
        )
        if extension_class is None or not isinstance(self.provider_config, extension_class):
            raise ValueError("provider_config requires a registered provider template extension")
        return self

    @classmethod
    def from_domain(cls, template: DomainTemplate) -> TemplateDTO:
        """Convert domain template to DTO."""
        template_dump = template.model_dump(mode="json", exclude_none=True)
        provider_type = getattr(template, "provider_type", None)
        provider_type_name = str(provider_type) if provider_type else None
        provider_config = None
        if provider_type_name:
            extension_class = TemplateExtensionRegistry.get_extension_class(provider_type_name)
            if extension_class is not None:
                dto_fields = set(cls.model_fields)
                provider_specific_config = {
                    key: value
                    for key, value in template_dump.items()
                    if key not in dto_fields and key != "metadata"
                }
                provider_config = extension_class(**provider_specific_config)

        return cls(
            # Core fields
            template_id=template.template_id,
            name=getattr(template, "name", None),
            description=getattr(template, "description", None),
            # Instance configuration
            instance_type=getattr(template, "instance_type", None),
            vm_size=getattr(template, "vm_size", None),
            vm_sizes=getattr(template, "vm_sizes", []) or [],
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
            placement_split_strategy=getattr(template, "placement_split_strategy", "hybrid"),
            placement_primary_share_percent=getattr(
                template, "placement_primary_share_percent", 80
            ),
            placement_regions=getattr(template, "placement_regions", []),
            placement_zones=getattr(template, "placement_zones", []),
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
            launch_template_id=getattr(template, "launch_template_id", None),
            # Advanced configuration
            monitoring_enabled=getattr(template, "monitoring_enabled", None),
            # Tags and metadata
            tags=getattr(template, "tags", {}),
            metadata=getattr(template, "metadata", {}),
            provider_config=provider_config,
            provider_data=getattr(template, "provider_data", {}),
            # Provider configuration
            provider_type=provider_type,
            provider_name=getattr(template, "provider_name", None),
            provider_api=getattr(template, "provider_api", None),
            # Timestamps
            created_at=getattr(template, "created_at", None),
            updated_at=getattr(template, "updated_at", None),
            # Active status
            is_active=getattr(template, "is_active", True),
            # Legacy fields
            version=getattr(template, "version", None),
        )

    def to_template_config(self) -> dict[str, Any]:
        """Return template config with provider-specific config promoted."""
        template_config = self.model_dump(mode="json", exclude_none=True)
        template_config.pop("provider_config", None)
        provider_config = (
            self.provider_config.model_dump(mode="json", exclude_none=True, exclude_unset=True)
            if self.provider_config is not None
            else {}
        )
        for key, value in provider_config.items():
            template_config.setdefault(key, value)
        return template_config


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
