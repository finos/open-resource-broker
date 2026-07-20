"""Template repository implementation using storage strategy composition."""

from datetime import datetime
from typing import Any, Optional

from orb.domain.template.repository import TemplateRepository as TemplateRepositoryInterface
from orb.domain.template.template_aggregate import Template
from orb.domain.template.value_objects import TemplateId
from orb.infrastructure.error.decorators import handle_infrastructure_exceptions
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.base.repository_mixin import StorageRepositoryMixin
from orb.infrastructure.storage.base.strategy import BaseStorageStrategy
from orb.infrastructure.storage.components import (
    EntityCache,
    EventPublisher,
    MemoryEntityCache,
    NoOpEventPublisher,
    NoOpVersionManager,
    VersionManager,
)
from orb.infrastructure.storage.components.entity_serializer import BaseEntitySerializer
from orb.infrastructure.storage.components.generic_serializer import GenericEntitySerializer


class TemplateSerializer(BaseEntitySerializer):
    """Handles Template aggregate serialization/deserialization."""

    def __init__(self, defaults_service=None) -> None:
        """Initialize the instance."""
        super().__init__()
        self._dt = GenericEntitySerializer(Template, "Template", "template_id")
        self.defaults_service = defaults_service

    @staticmethod
    def _apply_nullable_defaults(data: dict[str, Any]) -> dict[str, Any]:
        """Coerce nullable JSON columns to safe empty containers.

        Applied unconditionally so NULL values stored in legacy rows before
        the NOT NULL migration are never passed as Python None to from_dict
        for fields that expect a list or dict.
        """
        if data.get("tags") is None:
            data["tags"] = {}
        if data.get("metadata") is None:
            data["metadata"] = {}
        if data.get("security_group_ids") is None:
            data["security_group_ids"] = []
        if data.get("subnet_ids") is None:
            data["subnet_ids"] = []
        if data.get("network_zones") is None:
            data["network_zones"] = []
        if data.get("machine_types") is None:
            data["machine_types"] = {}
        if data.get("machine_types_ondemand") is None:
            data["machine_types_ondemand"] = {}
        if data.get("machine_types_priority") is None:
            data["machine_types_priority"] = {}
        return data

    def _normalize_machine_types(self, data: dict) -> dict[str, int]:
        """Normalize machine types from various input formats."""
        if "vmType" in data:
            return {data["vmType"]: 1}
        if "vmTypes" in data:
            return data["vmTypes"]
        if "instance_type" in data:
            return {data["instance_type"]: 1}
        if "instance_types" in data:
            return data["instance_types"]
        return {}

    @handle_infrastructure_exceptions(context="template_serialization")
    def to_dict(self, template: Template) -> dict[str, Any]:
        """Convert Template aggregate to dictionary with complete field support.

        The persistence column names are the storage layer's translation seam:
        the domain aggregate exposes ``machine_*`` fields while the SQL columns
        retain their original physical names (``image_id``, ``max_instances``,
        ``root_device_volume_size``, ``volume_type``, ``key_name``,
        ``user_data``, ``instance_profile``).  Serialising to the physical
        column names keeps values inside the SQL column allowlist so they are
        actually persisted; ``from_dict`` reconstructs the ``machine_*`` fields
        from these names on read.
        """
        try:
            return {
                "template_id": template.template_id,
                "name": template.name,
                "description": template.description,
                "image_id": template.machine_image,
                "max_instances": template.max_machines,
                "machine_types": template.machine_types,
                "machine_types_ondemand": template.machine_types_ondemand,
                "machine_types_priority": template.machine_types_priority,
                "subnet_ids": template.subnet_ids,
                "security_group_ids": template.security_group_ids,
                "network_zones": template.network_zones,
                "public_ip_assignment": template.public_ip_assignment,
                "root_device_volume_size": template.machine_disk_size_gb,
                "volume_type": template.machine_disk_type,
                "iops": template.iops,
                "throughput": template.throughput,
                "storage_encryption": template.storage_encryption,
                "encryption_key": template.encryption_key,
                "key_name": template.machine_ssh_key,
                "user_data": template.machine_bootstrap,
                "instance_profile": template.machine_role,
                "monitoring_enabled": template.monitoring_enabled,
                "price_type": template.price_type,
                "allocation_strategy": template.allocation_strategy,
                "max_price": template.max_price,
                "tags": template.tags,
                "metadata": template.metadata,
                "provider_type": template.provider_type,
                "provider_name": template.provider_name,
                "provider_api": template.provider_api,
                "is_active": template.is_active,
                "created_at": self._dt.serialize_datetime(template.created_at),
                "updated_at": self._dt.serialize_datetime(template.updated_at),
                "schema_version": "2.0.0",
            }
        except Exception as e:
            self.logger.error("Failed to serialize template %s: %s", template.template_id, e)
            raise

    @handle_infrastructure_exceptions(context="template_deserialization")
    def from_dict(self, data: dict[str, Any]) -> Template:
        """Convert dictionary to Template aggregate with complete field support."""
        try:
            self.logger.debug("Converting template data: %s", data)

            data = dict(data)  # shallow copy — never mutate the caller's dict
            data = self._apply_nullable_defaults(data)

            processed_data = data
            if self.defaults_service:
                try:
                    processed_data = self.defaults_service.resolve_template_defaults(
                        data, provider_name="default"
                    )
                    self.logger.debug("Applied configuration defaults to template data")
                except Exception as e:
                    self.logger.warning("Failed to apply defaults, using original data: %s", e)
                    processed_data = data

            now = datetime.now()
            created_at = self._dt.deserialize_datetime(processed_data.get("created_at")) or now
            updated_at = self._dt.deserialize_datetime(processed_data.get("updated_at")) or now

            template_id = processed_data.get("templateId", processed_data.get("template_id"))
            if not template_id:
                raise ValueError(f"No template_id found in data: {list(processed_data.keys())}")

            template_data = {
                "template_id": template_id,
                "name": processed_data.get("name", template_id),
                "description": processed_data.get("description"),
                "machine_image": processed_data.get(
                    "machine_image",
                    processed_data.get("imageId", processed_data.get("image_id")),
                ),
                "max_machines": processed_data.get(
                    "max_machines",
                    processed_data.get("maxNumber", processed_data.get("max_instances", 1)),
                ),
                "machine_types": self._normalize_machine_types(data),
                "machine_types_ondemand": data.get("vmTypesOnDemand", {}),
                "machine_types_priority": data.get("vmTypesPriority", {}),
                "subnet_ids": (
                    [processed_data.get("subnetId")]
                    if processed_data.get("subnetId")
                    else processed_data.get("subnet_ids", [])
                ),
                "security_group_ids": processed_data.get(
                    "securityGroupIds", processed_data.get("security_group_ids", [])
                ),
                "network_zones": processed_data.get("network_zones", []),
                "public_ip_assignment": processed_data.get("public_ip_assignment"),
                "machine_disk_size_gb": data.get(
                    "machine_disk_size_gb",
                    data.get("root_device_volume_size", data.get("root_volume_size")),
                ),
                "machine_disk_type": data.get(
                    "machine_disk_type", data.get("volume_type", data.get("root_volume_type"))
                ),
                "iops": data.get("iops", data.get("root_volume_iops")),
                "throughput": data.get("throughput", data.get("root_volume_throughput")),
                "storage_encryption": data.get("storage_encryption"),
                "encryption_key": data.get("encryption_key"),
                "machine_ssh_key": data.get(
                    "machine_ssh_key",
                    data.get("keyName", data.get("key_name", data.get("key_pair_name"))),
                ),
                "machine_bootstrap": data.get("machine_bootstrap", data.get("user_data")),
                "machine_role": data.get("machine_role") or data.get("instance_profile"),
                "monitoring_enabled": data.get("monitoring_enabled"),
                "price_type": data.get("price_type", "ondemand"),
                "allocation_strategy": data.get("allocation_strategy", "lowest_price"),
                "max_price": data.get("max_price"),
                "tags": data.get("tags", {}),
                "metadata": data.get("metadata", {}),
                "provider_type": data.get("provider_type"),
                "provider_name": data.get("provider_name"),
                "provider_api": data.get("providerApi", data.get("provider_api")),
                "is_active": data.get("is_active", True),
                "created_at": created_at,
                "updated_at": updated_at,
            }

            self.logger.debug("Converted template_data keys: %s", list(template_data.keys()))

            template = Template.model_validate(template_data)

            return template

        except Exception as e:
            self.logger.error("Failed to deserialize template data: %s", e)
            raise


class TemplateRepositoryImpl(StorageRepositoryMixin, TemplateRepositoryInterface):
    """Template repository implementation using storage strategy composition."""

    def __init__(
        self,
        storage_strategy: BaseStorageStrategy,
        cache: Optional[EntityCache] = None,
        event_publisher: Optional[EventPublisher] = None,
        version_manager: Optional[VersionManager] = None,
    ) -> None:
        """Initialize repository with storage strategy and optional components."""
        self.storage_strategy = storage_strategy
        self.serializer = TemplateSerializer()
        self.cache = cache or MemoryEntityCache()
        self.event_publisher = event_publisher or NoOpEventPublisher()
        self.version_manager = version_manager or NoOpVersionManager()
        self.logger = get_logger(__name__)

    @handle_infrastructure_exceptions(context="template_save")
    def save(self, template: Template) -> None:
        """Save template using storage strategy."""
        try:
            template_id_str = (
                str(template.template_id.value)  # type: ignore[union-attr]
                if hasattr(template.template_id, "value")
                else str(template.template_id)
            )
            version = self.version_manager.increment_version(template_id_str)

            template_data = self.serializer.to_dict(template)
            template_data["version"] = version
            self.storage_strategy.save(template_id_str, template_data)

            self.cache.put(template_id_str, template)

            events = template.get_domain_events() if hasattr(template, "get_domain_events") else []  # type: ignore[attr-defined]
            if hasattr(template, "clear_domain_events"):
                template.clear_domain_events()  # type: ignore[attr-defined]

            if events:
                self.event_publisher.publish_events(events)

            self.logger.debug(
                "Saved template %s (version %d) with %s events",
                template.template_id,
                version,
                len(events),
            )

        except Exception as e:
            self.logger.error("Failed to save template %s: %s", template.template_id, e)
            raise

    @handle_infrastructure_exceptions(context="template_retrieval")
    def get_by_id(self, template_id: TemplateId) -> Optional[Template]:
        """Get template by ID using storage strategy with caching."""
        try:
            key = str(template_id.value)

            cached = self.cache.get(key)
            if cached:
                self.logger.debug("Retrieved template %s from cache", template_id)
                return cached

            template = self._load_by_id(key)  # type: ignore[assignment]
            if template:
                self.cache.put(key, template)
            return template
        except Exception as e:
            self.logger.error("Failed to get template %s: %s", template_id, e)
            raise

    @handle_infrastructure_exceptions(context="template_retrieval")
    def find_by_id(self, template_id: TemplateId) -> Optional[Template]:
        """Find template by ID (alias for get_by_id)."""
        return self.get_by_id(template_id)

    @handle_infrastructure_exceptions(context="template_search")
    def find_by_template_id(self, template_id: str) -> Optional[Template]:
        """Find template by template ID string."""
        try:
            return self.get_by_id(TemplateId(value=template_id))
        except Exception as e:
            self.logger.error("Failed to find template by template_id %s: %s", template_id, e)
            raise

    @handle_infrastructure_exceptions(context="template_search")
    def find_by_name(self, name: str) -> Optional[Template]:
        """Find template by name."""
        try:
            results = self._load_by_criteria({"name": name})
            return results[0] if results else None  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find template by name %s: %s", name, e)
            raise

    @handle_infrastructure_exceptions(context="template_search")
    def find_active_templates(self) -> list[Template]:
        """Find active templates."""
        try:
            return self._load_by_criteria({"is_active": True})  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find active templates: %s", e)
            raise

    @handle_infrastructure_exceptions(context="template_search")
    def find_by_provider_api(self, provider_api: str) -> list[Template]:
        """Find templates by provider API."""
        try:
            return self._load_by_criteria({"provider_api": provider_api})  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find templates by provider_api %s: %s", provider_api, e)
            raise

    @handle_infrastructure_exceptions(context="template_search")
    def find_all(self) -> list[Template]:
        """Find all templates."""
        try:
            return self._load_all()  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find all templates: %s", e)
            raise

    def get_all(self) -> list[Template]:
        """Get all templates - alias for find_all for backward compatibility."""
        return self.find_all()

    def count_by_provider_api(self) -> dict[str, int]:
        """Return ``{provider_api: count}`` for all templates.

        Delegates to ``storage_strategy.count_by_column("provider_api")`` when
        the underlying strategy supports it (SQL fast path).  Falls back to the
        domain-interface default (list all + group) for file-based backends.
        """
        strategy = getattr(self, "storage_strategy", None)
        if strategy is not None and hasattr(strategy, "count_by_column"):
            result = strategy.count_by_column("provider_api")
            if result:
                return result
        # Slow path: list all rows and group in Python.
        return super().count_by_provider_api()

    @handle_infrastructure_exceptions(context="template_search")
    def search_templates(self, criteria: dict[str, Any]) -> list[Template]:
        """Search templates by criteria."""
        try:
            return self._load_by_criteria(criteria)  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to search templates with criteria %s: %s", criteria, e)
            raise

    @handle_infrastructure_exceptions(context="template_deletion")
    def delete(self, template_id: TemplateId) -> None:
        """Delete template by ID."""
        try:
            key = str(template_id.value)
            self._delete_by_id(key)
            self.cache.remove(key)
            self.logger.debug("Deleted template %s", template_id)
        except Exception as e:
            self.logger.error("Failed to delete template %s: %s", template_id, e)
            raise

    @handle_infrastructure_exceptions(context="template_existence_check")
    def exists(self, template_id: TemplateId) -> bool:
        """Check if template exists."""
        try:
            return self._check_exists(str(template_id.value))
        except Exception as e:
            self.logger.error("Failed to check if template %s exists: %s", template_id, e)
            raise
