"""GCP operation context resolution helpers."""

from __future__ import annotations

from typing import Mapping

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.providers.base.strategy import ProviderOperation
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.configuration.template_extension import GCPTemplateExtensionConfig
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.domain.template.value_objects import GCPProviderApi
from orb.providers.gcp.exceptions import GCPValidationError
from orb.providers.gcp.infrastructure.gcp_handler_factory import GCPHandlerFactory
from orb.providers.gcp.infrastructure.handlers.base_handler import GCPHandler
from orb.providers.gcp.services.operation_parameters import GCPMutationParameters
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

    @property
    def handler_factory(self) -> GCPHandlerFactory:
        """Expose the bound handler factory for strategy cache invalidation."""
        return self._handler_factory

    def build_create_context(
        self,
        operation: ProviderOperation,
    ) -> GCPCreateOperationContext:
        """Resolve a create operation into a typed context."""
        template_config = operation.parameters.get("template_config", {})
        count = int(operation.parameters.get("count", 1))
        if not template_config:
            raise GCPValidationError(
                "template_config is required for create_instances",
                error_code="MISSING_TEMPLATE_CONFIG",
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
        params = GCPMutationParameters.from_operation(operation)
        provider_api = params.provider_api_name
        handler_context = self._build_handler_context(params)
        resource_ids = params.resource_ids
        if provider_api == GCPProviderApi.MIG.value:
            resource_ids = self._resolve_mig_resource_ids(
                params=params,
                resource_ids=resource_ids,
                handler_context=handler_context,
            )
            if len(resource_ids) == 1:
                handler_context.setdefault("mig_name", resource_ids[0])
        return GCPMutationOperationContext(
            handler=self._get_handler_for_operation(params),
            instance_ids=params.instance_ids,
            resource_ids=resource_ids,
            handler_context=handler_context,
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

    def _get_handler_for_operation(self, params: GCPMutationParameters) -> GCPHandler:
        provider_api = params.provider_api_name
        if provider_api is None:
            provider_api = GCPProviderApi.SINGLE_VM.value
        return self._handler_factory.create_handler(provider_api)

    def _build_handler_context(self, params: GCPMutationParameters) -> GCPHandlerContext:
        """Build the provider-owned handler context from validated operation parameters."""
        context: GCPHandlerContext = {}
        provider_api = params.provider_api_name
        metadata = params.request_metadata

        if metadata.project_id is not None:
            context["project_id"] = metadata.project_id
        if metadata.region is not None:
            context["region"] = metadata.region
        if metadata.zone is not None:
            context["zone"] = metadata.zone
        if metadata.scope is not None:
            context["scope"] = metadata.scope
        if metadata.mig_name is not None:
            context["mig_name"] = metadata.mig_name
        if metadata.instance_template_name is not None:
            context["instance_template_name"] = metadata.instance_template_name
        if metadata.provider_api is not None:
            context["provider_api"] = metadata.provider_api.value
        context.setdefault("project_id", self._config.project_id)

        context.setdefault("region", params.region or self._config.region)

        zone = params.zone
        if zone is None:
            zone = self._zone_from_instance_resource_id(params.resource_id)
        if zone is None:
            zone = self._first_zone(params.zones or self._config.zones)
        if zone is not None:
            context.setdefault("zone", zone)

        resource_ids = params.resource_ids
        if len(resource_ids) == 1:
            context.setdefault("mig_name", resource_ids[0])

        if provider_api is not None:
            context.setdefault("provider_api", provider_api)
        return context

    def _resolve_mig_resource_ids(
        self,
        *,
        params: GCPMutationParameters,
        resource_ids: list[str],
        handler_context: GCPHandlerContext,
    ) -> list[str]:
        """Return explicit MIG resource IDs for a mutation/read operation."""
        if resource_ids:
            return resource_ids

        mapped_resource_ids = self._resource_ids_from_mapping(
            instance_ids=params.instance_ids,
            resource_mapping=params.resource_mapping,
        )
        if mapped_resource_ids:
            return mapped_resource_ids

        mig_name = handler_context.get("mig_name")
        if mig_name:
            return [mig_name]

        if params.instance_ids:
            raise GCPValidationError(
                "MIG operations with instance_ids require resource_mapping or resource_ids"
            )
        return []

    def _resource_ids_from_mapping(
        self,
        *,
        instance_ids: list[str],
        resource_mapping: dict[str, tuple[str, int]],
    ) -> list[str]:
        """Read MIG resource ownership from the operation resource mapping."""
        if not resource_mapping:
            return []

        resource_ids: list[str] = []
        for instance_id in instance_ids:
            mapping_value = resource_mapping.get(instance_id)
            if mapping_value is None:
                raise GCPValidationError(
                    f"resource_mapping is missing instance '{instance_id}'"
                )
            resource_id = mapping_value[0]
            if resource_id not in resource_ids:
                resource_ids.append(resource_id)
        return resource_ids

    @staticmethod
    def _zone_from_instance_resource_id(resource_id: str | None) -> str | None:
        """Extract the zone from a Compute Engine instance self-link or relative path."""
        if resource_id in (None, ""):
            return None
        parts = resource_id.split("/")
        try:
            zone_index = parts.index("zones") + 1
        except ValueError:
            return None
        if zone_index >= len(parts):
            return None
        return parts[zone_index] or None

    @staticmethod
    def _first_zone(zones: list[str]) -> str | None:
        """Return the first zone candidate from a zones collection, if any."""
        return zones[0] if zones else None
