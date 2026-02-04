"""Template DTOs for infrastructure layer - avoiding direct domain aggregate imports."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from application.dto.base import BaseDTO


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
    image_id: Optional[str] = None
    max_instances: int = 1

    # Network configuration
    subnet_ids: list[str] = Field(default_factory=list)
    security_group_ids: list[str] = Field(default_factory=list)

    # Pricing and allocation
    price_type: str = "ondemand"
    allocation_strategy: Optional[str] = None
    max_price: Optional[float] = None

    # Instance types configuration
    instance_types: dict[str, int] = Field(default_factory=dict)
    primary_instance_type: Optional[str] = None

    # Network configuration
    network_zones: list[str] = Field(default_factory=list)
    public_ip_assignment: Optional[bool] = None

    # Storage configuration
    root_volume_size: Optional[int] = None
    root_volume_type: Optional[str] = None
    root_volume_iops: Optional[int] = None
    root_volume_throughput: Optional[int] = None
    storage_encryption: Optional[bool] = None
    encryption_key: Optional[str] = None

    # Access and security
    key_pair_name: Optional[str] = None
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

    # Host Factory fields
    vm_type: Optional[str] = None
    vm_types: dict[str, Any] = Field(default_factory=dict)
    key_name: Optional[str] = None

    # Legacy fields
    configuration: dict[str, Any] = Field(default_factory=dict)
    version: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.template_id:
            raise ValueError("template_id is required")
        if not self.name:
            raise ValueError("name is required")
        if not self.provider_api:
            raise ValueError("provider_api is required")

    @classmethod
    def from_domain(cls, template) -> "TemplateDTO":
        """Convert domain template to DTO."""
        return cls(
            # Core fields
            template_id=template.template_id,
            name=getattr(template, 'name', None),
            description=getattr(template, 'description', None),
            
            # Instance configuration
            instance_type=getattr(template, 'instance_type', None),
            image_id=getattr(template, 'image_id', None),
            max_instances=getattr(template, 'max_instances', 1),
            
            # Network configuration
            subnet_ids=getattr(template, 'subnet_ids', []),
            security_group_ids=getattr(template, 'security_group_ids', []),
            
            # Pricing and allocation
            price_type=getattr(template, 'price_type', 'ondemand'),
            allocation_strategy=getattr(template, 'allocation_strategy', None),
            max_price=getattr(template, 'max_price', None),
            
            # Instance types configuration
            instance_types=getattr(template, 'instance_types', {}),
            primary_instance_type=getattr(template, 'primary_instance_type', None),
            
            # Network configuration
            network_zones=getattr(template, 'network_zones', []),
            public_ip_assignment=getattr(template, 'public_ip_assignment', None),
            
            # Storage configuration
            root_volume_size=getattr(template, 'root_volume_size', None),
            root_volume_type=getattr(template, 'root_volume_type', None),
            root_volume_iops=getattr(template, 'root_volume_iops', None),
            root_volume_throughput=getattr(template, 'root_volume_throughput', None),
            storage_encryption=getattr(template, 'storage_encryption', None),
            encryption_key=getattr(template, 'encryption_key', None),
            
            # Access and security
            key_pair_name=getattr(template, 'key_pair_name', None),
            user_data=getattr(template, 'user_data', None),
            instance_profile=getattr(template, 'instance_profile', None),
            
            # Advanced configuration
            monitoring_enabled=getattr(template, 'monitoring_enabled', None),
            
            # Tags and metadata
            tags=getattr(template, 'tags', {}),
            metadata=getattr(template, 'metadata', {}),
            
            # Provider configuration
            provider_type=getattr(template, 'provider_type', None),
            provider_name=getattr(template, 'provider_name', None),
            provider_api=getattr(template, 'provider_api', None),
            
            # Timestamps
            created_at=getattr(template, 'created_at', None),
            updated_at=getattr(template, 'updated_at', None),
            
            # Active status
            is_active=getattr(template, 'is_active', True),
            
            # Host Factory fields
            vm_type=getattr(template, 'vm_type', None),
            vm_types=getattr(template, 'vm_types', {}),
            key_name=getattr(template, 'key_name', None),
            
            # Legacy fields
            configuration=getattr(template, 'configuration', template.__dict__ if hasattr(template, '__dict__') else {}),
            version=getattr(template, 'version', None)
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
