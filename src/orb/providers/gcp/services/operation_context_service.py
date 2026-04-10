"""GCP operation context resolution helpers."""

from __future__ import annotations

from typing import Mapping

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.providers.base.strategy import ProviderOperation, ProviderResult
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.domain.template.value_objects import GCPProviderApi
from orb.providers.gcp.exceptions import GCPValidationError
from orb.providers.gcp.infrastructure.gcp_handler_factory import GCPHandlerFactory
from orb.providers.gcp.types import (
    GCPCreateOperationContext,
    GCPHandlerContext,
    GCPMutationOperationContext,
)


class GCPOperationContextService:
    """Resolve typed create and mutation contexts for GCP strategy operations."""

    def __init__(
        self,
        *,
        config: GCPProviderConfig,
        handler_factory: GCPHandlerFactory,
        provider_name: str | None,
    ) -> None:
        self._config = config
        self._handler_factory = handler_factory
        self._provider_name = provider_name

    def build_create_context(
        self,
        operation: ProviderOperation,
    ) -> GCPCreateOperationContext | ProviderResult:
        """Resolve a create operation into a typed context or an error result."""
        template_config = operation.parameters.get("template_config", {})
        count = int(operation.parameters.get("count", 1))
        if not template_config:
            return ProviderResult.error_result(
                "template_config is required for create_instances",
                "MISSING_TEMPLATE_CONFIG",
            )

        template = GCPTemplate.model_validate(self._build_gcp_template_config(template_config, count))
        handler = self._handler_factory.create_handler(template.provider_api)
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id=template.template_id,
            machine_count=count,
            provider_type="gcp",
            provider_name=self._provider_name,
            metadata=operation.parameters.get("request_metadata", {}),
            request_id=operation.parameters.get("request_id"),
        )
        request.provider_api = template.provider_api.value
        return GCPCreateOperationContext(
            template=template,
            request=request,
            handler=handler,
            count=count,
        )

    def build_mutation_context(self, operation: ProviderOperation) -> GCPMutationOperationContext:
        """Resolve a mutation/read operation into a typed handler dispatch context."""
        instance_ids = list(operation.parameters.get("instance_ids", []) or [])
        resource_ids = list(operation.parameters.get("resource_ids", []) or [])
        return GCPMutationOperationContext(
            handler=self._get_handler_for_operation(operation),
            instance_ids=instance_ids,
            resource_ids=resource_ids,
            handler_context=self._build_handler_context(operation),
        )

    def _build_gcp_template_config(
        self,
        template_config: Mapping[str, object],
        count: int,
    ) -> dict[str, object]:
        """Merge provider config and template defaults into one GCP template payload."""
        defaults = GCPTemplateExtensionConfig()
        merged = dict(template_config)

        # Provider identity and placement defaults come first so later sections can
        # freely add compute/network defaults without re-checking provider scope.
        merged.setdefault("provider_type", "gcp")
        merged.setdefault("provider_api", defaults.provider_api)
        merged.setdefault("project_id", self._config.project_id)
        merged.setdefault("region", self._config.region)
        merged.setdefault("zones", self._config.zones)

        # Network settings are provider-config driven unless the template overrides them.
        merged.setdefault("network", self._config.network)
        merged.setdefault("subnetwork", self._config.subnetwork)

        # Normalize legacy aliases before applying current compute defaults.
        if "instance_type" not in merged and "machine_type" in merged:
            merged["instance_type"] = merged["machine_type"]
        if "boot_disk_size_gb" not in merged and "root_device_volume_size" in merged:
            merged["boot_disk_size_gb"] = merged["root_device_volume_size"]
        if "boot_disk_type" not in merged and "volume_type" in merged:
            merged["boot_disk_type"] = merged["volume_type"]

        # Compute and image defaults describe the VM shape to provision.
        merged.setdefault("instance_type", defaults.machine_type)
        merged.setdefault("boot_disk_size_gb", defaults.boot_disk_size_gb)
        merged.setdefault("boot_disk_type", defaults.boot_disk_type)
        merged.setdefault("source_image_family", defaults.source_image_family)
        merged.setdefault("source_image_project", defaults.source_image_project)
        merged.setdefault("provisioning_model", defaults.provisioning_model)

        # Runtime metadata and request sizing are applied last so the caller's
        # explicit template values still win over provider-level defaults.
        merged.setdefault("network_tags", defaults.network_tags)
        merged.setdefault("labels", defaults.labels)
        merged.setdefault("instance_template_name_prefix", defaults.instance_template_name_prefix)
        merged.setdefault("max_instances", count)
        return merged

    def _get_handler_for_operation(self, operation: ProviderOperation):
        provider_api = operation.parameters.get("provider_api") or operation.parameters.get(
            "request_metadata", {}
        ).get("provider_api")
        if provider_api is None:
            provider_api = GCPProviderApi.SINGLE_VM.value
        return self._handler_factory.create_handler(provider_api)

    def _build_handler_context(self, operation: ProviderOperation) -> GCPHandlerContext:
        metadata = operation.parameters.get("request_metadata", {}) or {}
        if not isinstance(metadata, dict):
            raise GCPValidationError("request_metadata must be a mapping when provided")

        context: GCPHandlerContext = {}

        for key in (
            "project_id",
            "region",
            "zone",
            "scope",
            "mig_name",
            "instance_template_name",
            "provider_api",
        ):
            value = self._validate_optional_string(
                metadata.get(key),
                field_name=f"request_metadata.{key}",
            )
            if value is not None:
                context[key] = value

        context.setdefault("project_id", self._config.project_id)
        region = self._validate_optional_string(
            operation.parameters.get("region", self._config.region),
            field_name="region",
        )
        if region is not None:
            context.setdefault("region", region)

        zone = operation.parameters.get("zone")
        if zone is None:
            zones = operation.parameters.get("zones") or self._config.zones
            zone = self._first_zone(zones)
        zone = self._validate_optional_string(zone, field_name="zone")
        if zone is not None:
            context.setdefault("zone", zone)

        resource_ids = operation.parameters.get("resource_ids", []) or []
        if len(resource_ids) == 1:
            resource_id = self._validate_optional_string(
                resource_ids[0],
                field_name="resource_ids[0]",
            )
            if resource_id is not None:
                context.setdefault("mig_name", resource_id)

        provider_api = self._validate_optional_string(
            operation.parameters.get("provider_api"),
            field_name="provider_api",
        )
        if provider_api is not None:
            context.setdefault("provider_api", provider_api)
        return context

    @staticmethod
    def _validate_optional_string(value: object, *, field_name: str) -> str | None:
        """Return a string value or raise when the provided value has the wrong type."""
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise GCPValidationError(f"{field_name} must be a string")
        return value

    @staticmethod
    def _first_zone(zones: object) -> object:
        """Return the first zone candidate from a zones collection, if any."""
        if not zones:
            return None
        if not isinstance(zones, (list, tuple)):
            raise GCPValidationError("zones must be a list or tuple when provided")
        return zones[0] if zones else None
