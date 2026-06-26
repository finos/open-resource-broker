"""Kubernetes-specific typed DTO configuration for TemplateDTO serialisation.

Mirrors :mod:`orb.providers.aws.domain.template.aws_template_dto_config` for
the kubernetes provider.  Registered with :class:`TemplateExtensionRegistry`
so :meth:`TemplateDTO.from_domain` can delegate construction to the registry
rather than carrying kubernetes-specific knowledge directly.

Only kubernetes-specific fields belong here.  Generic fields that have a
home on the parent :class:`Template` are not duplicated:

* Container image lives in ``Template.image_id``.
* Operator labels live in ``Template.tags`` and are projected onto the
  pod label set at spec-build time.
* The per-request replica count comes from ``request.requested_count``;
  ``Template.max_instances`` caps the quota.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class K8sTemplateDTOConfig(BaseModel):
    """Typed container for kubernetes-specific fields on :class:`TemplateDTO`."""

    model_config = ConfigDict(extra="ignore")

    # Scheduling / placement
    namespace: Optional[str] = Field(
        None, description="Target namespace for this template's resources."
    )
    runtime_class: Optional[str] = Field(None, description="``runtimeClassName`` applied to pods.")
    service_account: Optional[str] = Field(
        None, description="``serviceAccountName`` applied to pods."
    )
    node_selector: Optional[dict[str, str]] = Field(
        None, description="``nodeSelector`` applied to pods."
    )
    tolerations: Optional[list[dict[str, Any]]] = Field(
        None, description="``tolerations`` applied to pods."
    )

    # Resource requests / limits
    resource_requests: Optional[dict[str, str]] = Field(
        None,
        description='Container resource requests, e.g. ``{"cpu": "500m", "memory": "1Gi"}``.',
    )
    resource_limits: Optional[dict[str, str]] = Field(
        None,
        description='Container resource limits, e.g. ``{"cpu": "2", "memory": "4Gi"}``.',
    )

    # Workload sizing overrides (Job-specific; replica count comes from
    # ``request.requested_count``)
    completions: Optional[int] = Field(
        None, description="``completions`` count for the Job handler."
    )
    parallelism: Optional[int] = Field(
        None, description="``parallelism`` count for the Job handler."
    )

    # Pod metadata
    annotations: Optional[dict[str, str]] = Field(
        None, description="Annotations applied to managed resources."
    )

    # Container environment / mounts
    environment_variables: Optional[dict[str, str]] = Field(
        None, description="Environment variables injected into the container."
    )
    volume_mounts: Optional[list[dict[str, Any]]] = Field(
        None, description="Volume mounts attached to the container."
    )
    volumes: Optional[list[dict[str, Any]]] = Field(
        None, description="Volumes declared on the pod spec."
    )

    # Optional command / args overrides
    command: Optional[list[str]] = Field(None, description="Container command override.")
    args: Optional[list[str]] = Field(None, description="Container args override.")

    # Image pull
    image_pull_secret: Optional[str] = Field(
        None, description="``imagePullSecrets`` entry attached to pods."
    )

    # Raw partial override applied AFTER the computed pod spec is built.
    pod_spec_override: Optional[dict[str, Any]] = Field(
        None,
        description=(
            "Partial pod-spec override deep-merged onto the computed pod spec at acquire time."
        ),
    )

    @field_validator("namespace")
    @classmethod
    def _validate_namespace(cls, v: Optional[str]) -> Optional[str]:
        """Empty namespace strings are rejected; ``None`` is the unset sentinel."""
        if v is not None and not v.strip():
            raise ValueError("namespace must be a non-empty string when set")
        return v

    @field_validator("completions", "parallelism")
    @classmethod
    def _validate_positive(cls, v: Optional[int]) -> Optional[int]:
        """Workload counts must be positive when set."""
        if v is not None and v <= 0:
            raise ValueError("workload count fields must be positive integers")
        return v

    def to_template_defaults(self) -> dict[str, Any]:
        """Return a flat dict of non-None values suitable for template defaults merging."""
        return {k: v for k, v in self.model_dump().items() if v is not None}
