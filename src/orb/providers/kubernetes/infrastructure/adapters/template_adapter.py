"""Kubernetes Template Adapter.

Mirrors :class:`orb.providers.aws.infrastructure.adapters.template_adapter.AWSTemplateAdapter`
for the kubernetes provider.  Provides kubernetes-specific template
operations (validation, field extension, supported-field introspection)
behind the generic :class:`TemplateAdapterPort` interface.

Kubernetes templates do not require AMI resolution or SSM lookups, so the
adapter is significantly thinner than the AWS counterpart.  The supported
fields list and validation rules cover the v1 kubernetes resource shape:
``container_image``, ``namespace``, resource requests / limits,
``runtime_class``, ``node_selector``, ``tolerations``, ``service_account``,
``replicas`` / ``completions`` / ``parallelism``, labels, annotations,
volume mounts, volumes, and environment variables.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.template_adapter_port import TemplateAdapterPort
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.dtos import TemplateDTO
from orb.providers.kubernetes.infrastructure.kubernetes_client import KubernetesClient

# Kubernetes resource-API names recognised by the v1 provider.  Templates
# carrying an unknown ``provider_api`` value are rejected during validation.
_SUPPORTED_PROVIDER_APIS: list[str] = [
    "KubernetesPod",
    "KubernetesDeployment",
    "KubernetesStatefulSet",
    "KubernetesJob",
]

# DNS-1123 label / subdomain pattern used for namespace / runtime-class
# validation.  Matches the kube-API restrictions in core/v1.
_DNS_1123_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

# Kubernetes resource-quantity pattern used to sanity-check cpu / memory /
# storage entries.  Mirrors the regular-expression in the legacy
# ``k8sutils.parse_quantity`` helper without importing it.
_QUANTITY = re.compile(
    r"^[+-]?(\d+(\.\d+)?|\.\d+)"  # numeric magnitude
    r"([eE][+-]?\d+)?"  # exponent
    r"([numµ]|[kKMGTPE]i?)?$"  # SI / binary suffix
)


class KubernetesTemplateAdapter(TemplateAdapterPort):
    """Kubernetes implementation of :class:`TemplateAdapterPort`."""

    # Fields the adapter recognises on the generic :class:`Template` or
    # under ``template.provider_data["kubernetes"]``.  Used by
    # :meth:`get_supported_fields` so the CLI / docs can introspect the
    # surface without reaching into the DTO config class.
    _SUPPORTED_FIELDS: list[str] = [
        "container_image",
        "namespace",
        "runtime_class",
        "node_selector",
        "tolerations",
        "service_account",
        "resource_requests",
        "resource_limits",
        "replicas",
        "completions",
        "parallelism",
        "labels",
        "annotations",
        "environment_variables",
        "volume_mounts",
        "volumes",
        "command",
        "args",
        "image_pull_secret",
    ]

    def __init__(
        self,
        template_config_manager: TemplateConfigurationManager,
        kubernetes_client: KubernetesClient,
        logger: LoggingPort,
    ) -> None:
        self._template_config_manager = template_config_manager
        self._kubernetes_client = kubernetes_client
        self._logger = logger

    # ------------------------------------------------------------------
    # Domain-level template operations
    # ------------------------------------------------------------------

    def validate_template(self, template: Template) -> list[str]:  # type: ignore[override]
        """Validate *template* for kubernetes-specific requirements.

        Returns a list of error messages — empty when the template is valid.
        """
        errors: list[str] = []
        errors.extend(self._validate_required_fields(template))
        errors.extend(err for err in self.validate_field_values(template).values() if err)
        errors.extend(self._validate_provider_api(template))
        return errors

    def extend_template_fields(self, template: Template) -> Template:
        """Attach kubernetes-specific provider data to *template* in place."""
        if not template.provider_api:
            template.provider_api = self.get_provider_api()

        if not template.provider_data:
            template.provider_data = {}

        if "kubernetes" not in template.provider_data:
            template.provider_data["kubernetes"] = {}

        template.provider_data["kubernetes"].update(
            {
                "supported_fields": self._SUPPORTED_FIELDS,
                "validation_enabled": True,
            }
        )

        return template

    def resolve_template_references(self, template: Template) -> Template:
        """Kubernetes templates have no provider-side references to resolve.

        Container images are pulled by the kubelet at pod start, so we do
        not attempt to validate or rewrite the image reference here.
        """
        return template

    def get_supported_fields(self) -> list[str]:
        """Return the list of kubernetes-specific template fields."""
        return self._SUPPORTED_FIELDS.copy()

    def validate_field_values(self, template: Template) -> dict[str, str]:
        """Validate kubernetes-specific field values on *template*.

        Returns a mapping of field name -> error message.  Empty values are
        reported as errors only when the field is required.
        """
        errors: dict[str, str] = {}

        k8s_block = self._kubernetes_block(template)

        # container_image: required either via provider_data or via the
        # legacy ``image_id`` field at the domain level.
        container_image = k8s_block.get("container_image") if k8s_block else None
        if not container_image and not getattr(template, "image_id", None):
            errors["container_image"] = (
                "Container image is required — set provider_data.kubernetes.container_image "
                "or template.image_id."
            )

        # namespace: optional but must conform to DNS-1123 when set
        namespace = (k8s_block or {}).get("namespace")
        if namespace is not None and not _DNS_1123_LABEL.match(str(namespace)):
            errors["namespace"] = f"Invalid namespace: {namespace!r}.  Must be a DNS-1123 label."

        # runtime_class follows the same rules as namespace
        runtime_class = (k8s_block or {}).get("runtime_class")
        if runtime_class is not None and not _DNS_1123_LABEL.match(str(runtime_class)):
            errors["runtime_class"] = (
                f"Invalid runtime_class: {runtime_class!r}.  Must be a DNS-1123 label."
            )

        # resource_requests / resource_limits: each value must parse as a
        # kubernetes resource quantity (e.g. "500m", "1Gi", "2").
        for field in ("resource_requests", "resource_limits"):
            entries = (k8s_block or {}).get(field) or {}
            if not isinstance(entries, dict):
                errors[field] = f"{field} must be a mapping of resource -> quantity"
                continue
            for resource, quantity in entries.items():
                if not _QUANTITY.match(str(quantity)):
                    errors[field] = (
                        f"Invalid {field} entry for {resource!r}: {quantity!r} is not a "
                        f"valid kubernetes resource quantity."
                    )
                    break

        # Workload sizing: replicas / completions / parallelism must be > 0 when set
        for field in ("replicas", "completions", "parallelism"):
            value = (k8s_block or {}).get(field)
            if value is not None:
                try:
                    if int(value) <= 0:
                        errors[field] = f"{field} must be a positive integer"
                except (TypeError, ValueError):
                    errors[field] = f"{field} must be an integer"

        return errors

    def get_provider_api(self) -> str:
        """Return the default kubernetes provider API identifier."""
        return "KubernetesPod"

    # ------------------------------------------------------------------
    # Port interface — TemplateDTO surface
    # ------------------------------------------------------------------

    async def get_template_by_id(self, template_id: str) -> Optional[TemplateDTO]:  # type: ignore[override]
        return await self._template_config_manager.get_template_by_id(template_id)

    async def get_all_templates(self) -> list[TemplateDTO]:  # type: ignore[override]
        return await self._template_config_manager.get_all_templates()

    async def get_templates_by_provider_api(self, provider_api: str) -> list[TemplateDTO]:  # type: ignore[override]
        return await self._template_config_manager.get_templates_by_provider(provider_api)

    async def validate_template_dto(self, template: TemplateDTO) -> dict[str, Any]:
        return await self._template_config_manager.validate_template(template)

    async def save_template(self, template: TemplateDTO) -> None:  # type: ignore[override]
        await self._template_config_manager.save_template(template)

    async def delete_template(self, template_id: str) -> None:
        await self._template_config_manager.delete_template(template_id)

    def get_supported_provider_apis(self) -> list[str]:
        """Return the static list of kubernetes resource APIs supported by v1."""
        return list(_SUPPORTED_PROVIDER_APIS)

    def get_adapter_info(self) -> dict[str, Any]:
        """Return metadata describing this adapter for diagnostic purposes."""
        return {
            "adapter_name": "KubernetesTemplateAdapter",
            "provider_type": "kubernetes",
            "supported_apis": self.get_supported_provider_apis(),
            "supported_fields": self._SUPPORTED_FIELDS,
            "features": [
                "field_validation",
                "resource_quantity_validation",
                "dns1123_validation",
            ],
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _kubernetes_block(template: Template) -> Optional[dict[str, Any]]:
        """Return the ``provider_data["kubernetes"]`` dict on *template*, or ``None``."""
        provider_data = getattr(template, "provider_data", None) or {}
        if not isinstance(provider_data, dict):
            return None
        block = provider_data.get("kubernetes")
        if not isinstance(block, dict):
            return None
        return block

    def _validate_required_fields(self, template: Template) -> list[str]:
        """Validate fields that are strictly required for any kubernetes template."""
        errors: list[str] = []
        if not template.template_id:
            errors.append("template_id is required for kubernetes templates")
        return errors

    def _validate_provider_api(self, template: Template) -> list[str]:
        """Reject templates carrying an unknown kubernetes provider API."""
        provider_api = template.provider_api
        if provider_api is None:
            return []
        if provider_api not in _SUPPORTED_PROVIDER_APIS:
            return [
                f"Unsupported kubernetes provider_api: {provider_api!r}. "
                f"Must be one of {_SUPPORTED_PROVIDER_APIS}."
            ]
        return []


def create_kubernetes_template_adapter(
    kubernetes_client: KubernetesClient,
    logger: LoggingPort,
    config: ConfigurationPort,
) -> KubernetesTemplateAdapter:
    """Construct a :class:`KubernetesTemplateAdapter` with its template-config manager.

    Mirrors :func:`orb.providers.aws.infrastructure.adapters.template_adapter.create_aws_template_adapter`
    so the DI container registration in :mod:`registration` can use the same
    callable shape.
    """
    template_config_manager = TemplateConfigurationManager(kubernetes_client, logger)  # type: ignore[arg-type]
    return KubernetesTemplateAdapter(template_config_manager, kubernetes_client, logger)
