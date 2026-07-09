"""TemplateDTO — application-layer data transfer object for templates.

This module owns the TemplateDTO class.  The ``from_domain`` factory method
(which depends on ``TemplateExtensionRegistry`` from infrastructure) lives in
``orb.infrastructure.template.factories.TemplateDTOFactory`` so that this
module stays free of infrastructure imports.

``orb.infrastructure.template.dtos`` is now a thin shim that re-exports this
class for backward-compat with infra-layer callers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_serializer, model_validator

from orb.application.dto.base import BaseDTO


class TemplateDTO(BaseDTO):
    """
    Template Data Transfer Object.

    Lives in the application layer so that command/query handlers and
    orchestrators can import it without touching infrastructure.  The
    provider-specific ``from_domain`` conversion (which needs
    ``TemplateExtensionRegistry``) is handled by
    ``orb.infrastructure.template.factories.TemplateDTOFactory``.
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
        return value.model_dump()

    @model_validator(mode="before")
    @classmethod
    def _set_defaults(cls, data: Any) -> Any:
        """Set default values for optional fields derived from other fields."""
        if isinstance(data, dict):
            if not data.get("name"):
                data["name"] = data.get("template_id")
        return data
