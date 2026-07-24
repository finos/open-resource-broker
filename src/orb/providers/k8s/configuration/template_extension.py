"""Kubernetes-specific template extension configuration.

Mirrors :mod:`orb.providers.aws.configuration.template_extension` for the
kubernetes provider.  Holds the kubernetes-specific defaults that are
merged into the hierarchical template defaults pipeline so that handlers
receive a fully-populated template at runtime.

The :class:`K8sTemplateExtensionConfig` is registered with
:class:`TemplateExtensionRegistry` during provider bootstrap.

Shadow fields removed — generic concepts read from the parent ``Template``:

* The replica count comes from ``request.requested_count`` at acquire
  time; ``max_machines`` on the generic ``Template`` caps the quota.
* Container images come from ``Template.machine_image``.
* Pod labels are projected from ``Template.tags`` at spec-build time.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import AliasChoices, Field, field_validator, model_validator

from orb.providers.base.template_extension import ProviderTemplateExtensionBase

logger = logging.getLogger(__name__)


class K8sTemplateExtensionConfig(ProviderTemplateExtensionBase):
    """Kubernetes-specific template extension configuration.

    These fields are applied to kubernetes templates through the
    hierarchical defaults system in addition to (and after) the
    provider-level :class:`K8sProviderConfig` defaults.  Operator-level
    overrides on the template itself win against this baseline.
    """

    # ``model_config = ConfigDict(extra="ignore")`` is inherited from
    # ProviderTemplateExtensionBase.

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
    # Field name is ``env`` (matching K8sTemplate.env).  The legacy name
    # ``environment_variables`` is accepted as a back-compat alias so
    # existing operator YAML using the old spelling still parses.  The
    # ``_warn_deprecated_field_names`` validator below surfaces the deprecation
    # to operators; ``AliasChoices`` alone accepts the old key silently.
    env: Optional[dict[str, str]] = Field(
        None,
        validation_alias=AliasChoices("env", "environment_variables"),
        description="Default environment variables injected into the container (dict[str, str] wire form).",
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

    # Pod scheduling priority
    priority_class_name: Optional[str] = Field(
        None, description="Default ``priorityClassName`` applied to managed pods."
    )

    # Pod termination
    termination_grace_period_seconds: Optional[int] = Field(
        None, description="Default ``terminationGracePeriodSeconds`` applied to managed pods."
    )

    # Pod restart policy
    restart_policy: Optional[str] = Field(
        None,
        description=(
            "Default ``restartPolicy`` applied to managed pods.  One of "
            "'Always' / 'OnFailure' / 'Never'; per-kind constraints apply at build time."
        ),
    )

    # Container health probes
    readiness_probe: Optional[dict[str, Any]] = Field(
        None, description="Default readiness probe applied to managed containers."
    )
    liveness_probe: Optional[dict[str, Any]] = Field(
        None, description="Default liveness probe applied to managed containers."
    )

    # Pod-level security context
    security_context: Optional[dict[str, Any]] = Field(
        None, description="Default pod-level ``securityContext`` applied to managed pods."
    )

    # Job lifecycle defaults
    ttl_seconds_after_finished: Optional[int] = Field(
        None, description="Default ``ttlSecondsAfterFinished`` for the Job handler."
    )
    active_deadline_seconds: Optional[int] = Field(
        None, description="Default ``activeDeadlineSeconds`` for the Job handler."
    )

    @model_validator(mode="before")
    @classmethod
    def _warn_deprecated_field_names(cls, data: Any) -> Any:
        """Emit an operator-facing warning for the deprecated ``environment_variables`` key.

        This is operator config surface (deserialised from provider YAML/JSON),
        so the signal must reach operators via ``logger.warning`` — a
        ``DeprecationWarning`` is filtered in production and never surfaces.
        Runs on the raw input before ``AliasChoices`` maps the old key.
        """
        if isinstance(data, dict) and "environment_variables" in data and "env" not in data:
            logger.warning(
                "K8s template config field 'environment_variables' is deprecated; "
                "use 'env' instead."
            )
        return data

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
