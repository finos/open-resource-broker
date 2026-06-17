"""Azure-specific native spec processing."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from orb.application.services.native_spec_service import NativeSpecService
from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.aggregate import Request
from orb.infrastructure.utilities.common.deep_merge import deep_merge
from orb.infrastructure.utilities.file.json_utils import read_json_file
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate


class _AzureNativeSpecConfig(BaseModel):
    """Azure native spec extension config owned by the Azure native spec subsystem."""

    spec_file_base_path: str = Field(default="specs/azure")


def _default_azure_native_spec_config() -> _AzureNativeSpecConfig:
    """Create the default Azure native spec extension config."""
    return _AzureNativeSpecConfig()


class _AzureProviderExtensionsConfig(BaseModel):
    """Azure provider extensions consumed by the Azure native spec subsystem."""

    model_config = ConfigDict(extra="ignore")

    native_spec: _AzureNativeSpecConfig = Field(default_factory=_default_azure_native_spec_config)


@injectable
class AzureNativeSpecService:
    """Azure-specific native spec processing for VMSS and SingleVM payloads."""

    def __init__(self, native_spec_service: NativeSpecService, config_port: ConfigurationPort):
        self.native_spec_service = native_spec_service
        self.config_port = config_port

    def process_provider_api_spec_with_merge(
        self,
        template: AzureTemplate,
        request: Request,
        default_payload: dict[str, Any],
        extra_context: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Render and optionally merge an Azure provider API spec."""
        if not self.native_spec_service.is_native_spec_enabled():
            return None

        native_spec = self._resolve_provider_api_spec(template)
        if not native_spec:
            return None

        context = self._build_azure_context(template, request)
        if extra_context:
            context.update(extra_context)

        rendered_native_spec = self.native_spec_service.render_spec(native_spec, context)

        native_config = self.config_port.get_native_spec_config() or {}
        merge_mode = native_config.get("merge_mode", "merge")

        if merge_mode == "replace":
            return rendered_native_spec
        if merge_mode == "merge":
            return deep_merge(default_payload, rendered_native_spec)
        return rendered_native_spec

    def _resolve_provider_api_spec(self, template: AzureTemplate) -> Optional[dict[str, Any]]:
        """Resolve provider API spec from inline data or file path."""
        if template.provider_api_spec:
            return template.provider_api_spec
        if template.provider_api_spec_file:
            return self._load_spec_file(template.provider_api_spec_file)
        return None

    def _load_spec_file(self, file_path: str) -> dict[str, Any]:
        """Load Azure native spec file."""
        provider_config = self.config_port.get_provider_config()
        azure_defaults = None
        if provider_config:
            azure_defaults = provider_config.provider_defaults.get("azure")

        base_path = _AzureNativeSpecConfig.model_fields["spec_file_base_path"].default
        if azure_defaults and azure_defaults.extensions:
            azure_extensions = _AzureProviderExtensionsConfig.model_validate(
                azure_defaults.extensions
            )
            base_path = azure_extensions.native_spec.spec_file_base_path

        return read_json_file(f"{base_path}/{file_path}")

    def _build_azure_context(self, template: AzureTemplate, request: Request) -> dict[str, Any]:
        """Build Azure-specific native spec rendering context."""
        package_info = self.config_port.get_package_info() or {}

        return {
            "request_id": str(request.request_id),
            "requested_count": request.requested_count,
            "template_id": template.template_id,
            "provider_api": template.provider_api.value,
            "resource_group": template.resource_group.value,
            "location": template.location.value,
            "vm_size": template.vm_size,
            "package_name": package_info.get("name", "open-resource-broker"),
            "package_version": package_info.get("version", "unknown"),
        }
