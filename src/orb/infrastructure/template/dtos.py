"""Template DTOs for infrastructure layer - avoiding direct domain aggregate imports."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_serializer, model_validator

from orb.application.dto.base import BaseDTO
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry


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

    # Typed provider-specific configuration, populated via TemplateExtensionRegistry
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
        """Serialise the typed provider_config to a plain dict for model_dump() consumers."""
        if value is None:
            return None
        return value.model_dump(exclude_none=True)

    @model_validator(mode="before")
    @classmethod
    def _set_defaults(cls, data: Any) -> Any:
        """Set defaults and validate provider-specific configuration."""
        if isinstance(data, dict):
            data = dict(data)
            if not data.get("name"):
                data["name"] = data.get("template_id")

            provider_config = data.get("provider_config")
            provider_type = data.get("provider_type")
            if provider_config is not None:
                if not provider_type:
                    raise ValueError("provider_type is required when provider_config is supplied")

                provider_type_key = str(provider_type)
                extension_class = TemplateExtensionRegistry.get_extension_class(provider_type_key)
                if extension_class is None:
                    raise ValueError(
                        f"provider_config supplied for unregistered provider {provider_type_key!r}"
                    )

                if isinstance(provider_config, dict):
                    data["provider_config"] = extension_class.model_validate(provider_config)
                elif not isinstance(provider_config, extension_class):
                    raise ValueError(
                        "provider_config type does not match the registered "
                        f"extension for provider {provider_type_key!r}"
                    )
        return data

    def to_template_config(self) -> dict[str, Any]:
        """Convert this DTO to flat template data for ``TemplateFactory``."""
        data = self.model_dump(mode="python", exclude_none=True)
        provider_config = data.pop("provider_config", None)
        if provider_config is None:
            return data

        if isinstance(provider_config, BaseModel):
            provider_config_data = provider_config.model_dump(exclude_none=True)
        else:
            provider_config_data = {
                key: value for key, value in dict(provider_config).items() if value is not None
            }

        return {**provider_config_data, **data}

    @classmethod
    def from_domain(cls, template) -> "TemplateDTO":
        """Convert domain template to DTO."""
        provider_type = getattr(template, "provider_type", None)
        provider_config: Optional[BaseModel] = None
        if provider_type:
            template_data = (
                template.model_dump() if hasattr(template, "model_dump") else vars(template)
            )
            provider_config = TemplateExtensionRegistry.create_extension_config(
                str(provider_type), template_data
            )

        return cls(
            # Core fields
            template_id=template.template_id,
            name=getattr(template, "name", None),
            description=getattr(template, "description", None),
            # Instance configuration
            image_id=getattr(template, "image_id", None),
            max_instances=getattr(template, "max_instances", 1),
            # Machine types configuration
            machine_types=getattr(template, "machine_types", {}),
            machine_types_ondemand=getattr(template, "machine_types_ondemand", {}),
            machine_types_priority=getattr(template, "machine_types_priority", {}),
            # Network configuration
            subnet_ids=getattr(template, "subnet_ids", []),
            security_group_ids=getattr(template, "security_group_ids", []),
            network_zones=getattr(template, "network_zones", []),
            public_ip_assignment=getattr(template, "public_ip_assignment", None),
            # Pricing and allocation
            price_type=getattr(template, "price_type", "ondemand"),
            allocation_strategy=getattr(template, "allocation_strategy", None),
            max_price=getattr(template, "max_price", None),
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
            # Metadata
            tags=getattr(template, "tags", {}),
            metadata=getattr(template, "metadata", {}),
            provider_config=provider_config,
            provider_data=getattr(template, "provider_data", {}),
            # Provider identification
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
