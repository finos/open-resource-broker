"""Infrastructure factory for converting domain templates to TemplateDTOs.

The ``from_domain`` conversion requires ``TemplateExtensionRegistry``, which is
an infrastructure concern, so it lives here rather than in the application-layer
``TemplateDTO`` class.  Callers that need to convert a domain ``Template`` to a
``TemplateDTO`` should inject ``TemplateDTOFactory`` and call
``factory.from_domain(template)``.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from orb.application.dto.template import TemplateDTO
from orb.application.ports.template_dto_factory_port import TemplateDTOFactoryPort
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry


class TemplateDTOFactory(TemplateDTOFactoryPort):
    """Converts domain Template aggregates to TemplateDTO instances.

    Delegates provider-specific field extraction to the
    ``TemplateExtensionRegistry`` so the factory itself stays
    provider-agnostic.
    """

    def from_domain(self, template: Any) -> TemplateDTO:
        """Convert a domain template aggregate to a ``TemplateDTO``.

        Args:
            template: Any domain template object (Template or provider subclass).

        Returns:
            A fully populated ``TemplateDTO``.
        """
        _provider_type = getattr(template, "provider_type", None)
        _provider_config: Optional[BaseModel] = None
        if _provider_type:
            # Serialise the domain object to a plain dict so the extension
            # class can pick up its own fields (with extra="ignore").
            if hasattr(template, "model_dump"):
                _template_data = template.model_dump()
            else:
                _template_data = vars(template)
            _provider_config = TemplateExtensionRegistry.create_extension_config(
                _provider_type, _template_data
            )

        return TemplateDTO(
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
            # Tags and metadata (cross-provider opaque data only)
            tags=getattr(template, "tags", {}),
            metadata=getattr(template, "metadata", {}),
            # Typed provider-specific configuration (populated via registry)
            provider_config=_provider_config,
            provider_data=getattr(template, "provider_data", {}),
            # Provider identification
            provider_type=_provider_type,
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
