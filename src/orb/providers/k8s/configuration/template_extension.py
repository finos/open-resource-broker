"""Kubernetes-specific template extension configuration.

Mirrors :mod:`orb.providers.aws.configuration.template_extension` for the
kubernetes provider.  Holds the kubernetes-specific defaults that are
merged into the hierarchical template defaults pipeline so that handlers
receive a fully-populated template at runtime.

The :class:`K8sTemplateExtensionConfig` is registered with
:class:`TemplateExtensionRegistry` during provider bootstrap.

Shadow fields removed — generic concepts read from the parent ``Template``:

* The replica count comes from ``request.requested_count`` at acquire
  time; ``max_instances`` on the generic ``Template`` caps the quota.
* Container images come from ``Template.image_id``.
* Pod labels are projected from ``Template.tags`` at spec-build time.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class K8sTemplateExtensionConfig(BaseModel):
    """Kubernetes-specific template extension configuration.

    These fields are applied to kubernetes templates through the
    hierarchical defaults system in addition to (and after) the
    provider-level :class:`K8sProviderConfig` defaults.  Operator-level
    overrides on the template itself win against this baseline.
    """

    model_config = ConfigDict(extra="ignore")

    # Workload sizing overrides for the Job handler.  The per-request
    # replica count is taken from ``request.requested_count`` — these
    # fields exist to allow operator-level overrides when needed.
    completions: Optional[int] = Field(
        None, description="Default ``completions`` for the Job handler."
    )
    parallelism: Optional[int] = Field(
        None, description="Default ``parallelism`` for the Job handler."
    )

    # Scheduling defaults
    namespace: Optional[str] = Field(
        None,
        description=(
            "Default namespace for templates that omit one.  Falls back to "
            "``K8sProviderConfig.namespace`` when unset."
        ),
    )
    runtime_class: Optional[str] = Field(
        None, description="Default ``runtimeClassName`` applied to managed pods."
    )
    service_account: Optional[str] = Field(
        None,
        description="Default ``serviceAccountName`` applied to managed pods.",
    )
    node_selector: Optional[dict[str, str]] = Field(
        None, description="Default ``nodeSelector`` applied to managed pods."
    )
    tolerations: Optional[list[dict[str, Any]]] = Field(
        None, description="Default ``tolerations`` applied to managed pods."
    )

    # Resource defaults
    resource_requests: Optional[dict[str, str]] = Field(
        None, description="Default container resource requests (e.g. cpu / memory)."
    )
    resource_limits: Optional[dict[str, str]] = Field(
        None, description="Default container resource limits (e.g. cpu / memory)."
    )

    # Pod metadata
    annotations: Optional[dict[str, str]] = Field(
        None, description="Default annotations applied to managed resources."
    )

    # Container environment / mounts
    environment_variables: Optional[dict[str, str]] = Field(
        None, description="Default environment variables injected into the container."
    )
    volume_mounts: Optional[list[dict[str, Any]]] = Field(
        None, description="Default volume mounts attached to the container."
    )
    volumes: Optional[list[dict[str, Any]]] = Field(
        None, description="Default volumes declared on the pod spec."
    )

    # Image pull defaults
    image_pull_secret: Optional[str] = Field(
        None,
        description=(
            "Default ``imagePullSecrets`` entry attached to managed pods.  "
            "Falls back to ``K8sProviderConfig.default_image_pull_secret`` when unset."
        ),
    )

    @field_validator("namespace")
    @classmethod
    def _validate_namespace(cls, v: Optional[str]) -> Optional[str]:
        """Reject the empty string explicitly; ``None`` is the unset sentinel."""
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
        """Convert the extension config to a flat template-defaults dict.

        Drops ``None`` entries so the hierarchical merge only contributes
        keys that the operator actually set.
        """
        return {k: v for k, v in self.model_dump().items() if v is not None}
