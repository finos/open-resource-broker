"""Kubernetes Template Adapter.

Mirrors :class:`orb.providers.aws.infrastructure.adapters.template_adapter.AWSTemplateAdapter`
for the kubernetes provider.  Provides kubernetes-specific template
operations (validation, field extension, supported-field introspection)
behind the generic :class:`TemplateAdapterPort` interface.

Kubernetes templates do not require AMI resolution or SSM lookups, so the
adapter is significantly thinner than the AWS counterpart.  The supported
fields list and validation rules cover the v1 kubernetes resource shape
exposed via :class:`K8sTemplate`: ``namespace``, resource requests /
limits, ``runtime_class``, ``node_selector``, ``tolerations``,
``service_account``, ``completions`` / ``parallelism``, annotations,
env vars, volume mounts, and volumes.  Generic concepts such as the
container image (``Template.image_id``) and operator labels
(``Template.tags``) come from the parent ``Template`` and are not
duplicated here.
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
from orb.providers.k8s.infrastructure.k8s_client import K8sClient

# Kubernetes resource-API names recognised by the v1 provider.  Templates
# carrying an unknown ``provider_api`` value are rejected during validation.
_SUPPORTED_PROVIDER_APIS: list[str] = [
    "Pod",
    "Deployment",
    "StatefulSet",
    "Job",
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


class K8sTemplateAdapter(TemplateAdapterPort):
    """Kubernetes implementation of :class:`TemplateAdapterPort`."""

    # Fields the adapter recognises on :class:`K8sTemplate` (typed
    # kubernetes-specific extensions).  Generic fields like ``image_id``
    # and ``tags`` come from the parent :class:`Template` and are not
    # listed here.  Used by :meth:`get_supported_fields` so the CLI /
    # docs can introspect the surface without reaching into the DTO
    # config class.
    _SUPPORTED_FIELDS: list[str] = [
        "namespace",
        "runtime_class",
        "node_selector",
        "tolerations",
        "service_account",
        "resource_requests",
        "resource_limits",
        "completions",
        "parallelism",
        "annotations",
        "env",
        "volume_mounts",
        "volumes",
        "command",
        "args",
        "image_pull_secret",
        "pod_spec_override",
        "priority_class_name",
        "termination_grace_period_seconds",
        "readiness_probe",
        "liveness_probe",
        "security_context",
        "ttl_seconds_after_finished",
        "active_deadline_seconds",
    ]

    def __init__(
        self,
        template_config_manager: TemplateConfigurationManager,
        kubernetes_client: K8sClient,
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
        """Attach kubernetes-specific provider data to *template* in place.

        With the typed :class:`K8sTemplate` aggregate in place, the
        kubernetes provider does not need to mirror operator-supplied
        fields into ``template.provider_data["k8s"]`` — handlers read
        the typed fields directly.  We still default ``provider_api``
        to ``"Pod"`` when the template arrives without one.
        """
        if not template.provider_api:
            template.provider_api = self.get_provider_api()
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

        Reads the typed :class:`K8sTemplate` fields directly.  Returns a
        mapping of field name -> error message; empty when valid.
        """
        from orb.providers.k8s.domain.template.k8s_template import (
            upcast_to_k8s_template,
        )

        errors: dict[str, str] = {}
        k8s_template = upcast_to_k8s_template(template)

        # Container image required via the generic ``Template.image_id`` field.
        if not getattr(template, "image_id", None):
            errors["image_id"] = (
                "Container image is required — set Template.image_id on the kubernetes template."
            )

        # namespace: optional but must conform to DNS-1123 when set
        namespace = k8s_template.namespace
        if namespace is not None and not _DNS_1123_LABEL.match(str(namespace)):
            errors["namespace"] = f"Invalid namespace: {namespace!r}.  Must be a DNS-1123 label."

        # runtime_class follows the same rules as namespace
        runtime_class = k8s_template.runtime_class
        if runtime_class is not None and not _DNS_1123_LABEL.match(str(runtime_class)):
            errors["runtime_class"] = (
                f"Invalid runtime_class: {runtime_class!r}.  Must be a DNS-1123 label."
            )

        # resource_requests / resource_limits: each emitted entry must
        # parse as a kubernetes resource quantity (e.g. "500m", "1Gi").
        for field_name, payload in (
            ("resource_requests", k8s_template.resolve_resource_requests_map()),
            ("resource_limits", k8s_template.resolve_resource_limits_map()),
        ):
            if not payload:
                continue
            for resource, quantity in payload.items():
                if not _QUANTITY.match(str(quantity)):
                    errors[field_name] = (
                        f"Invalid {field_name} entry for {resource!r}: "
                        f"{quantity!r} is not a valid kubernetes resource quantity."
                    )
                    break

        # Workload sizing — only completions / parallelism are operator
        # surfaces.  ``Template.max_instances`` cap is enforced by the
        # handler at acquire time; replica count comes from
        # ``request.requested_count``.
        for field_name, value in (
            ("completions", k8s_template.completions),
            ("parallelism", k8s_template.parallelism),
        ):
            if value is None:
                continue
            try:
                if int(value) <= 0:
                    errors[field_name] = f"{field_name} must be a positive integer"
            except (TypeError, ValueError):
                errors[field_name] = f"{field_name} must be an integer"

        return errors

    def get_provider_api(self) -> str:
        """Return the default kubernetes provider API identifier."""
        return "Pod"

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
            "adapter_name": "K8sTemplateAdapter",
            "provider_type": "k8s",
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


def create_k8s_template_adapter(
    kubernetes_client: K8sClient,
    logger: LoggingPort,
    config: ConfigurationPort,
) -> K8sTemplateAdapter:
    """Construct a :class:`K8sTemplateAdapter` with its template-config manager.

    Mirrors :func:`orb.providers.aws.infrastructure.adapters.template_adapter.create_aws_template_adapter`
    so the DI container registration in :mod:`registration` can use the same
    callable shape.
    """
    template_config_manager = TemplateConfigurationManager(kubernetes_client, logger, config)  # type: ignore[arg-type]
    return K8sTemplateAdapter(template_config_manager, kubernetes_client, logger)
