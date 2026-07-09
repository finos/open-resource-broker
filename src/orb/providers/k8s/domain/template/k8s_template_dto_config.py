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

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


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

    # Resource requests / limits.  Values are quantity strings on the wire
    # (``"500m"``, ``"1Gi"``) but the DTO accepts ``Any`` so the
    # ``K8sResourceQuantities.model_dump()`` round-trip — which emits
    # ``None`` for unset accelerator fields (``ephemeral_storage``,
    # ``gpu_count``, ``gpu_type``) — does not fail validation.
    resource_requests: Optional[dict[str, Any]] = Field(
        None,
        description='Container resource requests, e.g. ``{"cpu": "500m", "memory": "1Gi"}``.',
    )
    resource_limits: Optional[dict[str, Any]] = Field(
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
    # Field name is ``env`` (matching K8sTemplate.env).  The legacy name
    # ``environment_variables`` is accepted as a back-compat alias so
    # existing operator YAML and serialised TemplateDTO dicts still parse.
    env: Optional[dict[str, str]] = Field(
        None,
        validation_alias=AliasChoices("env", "environment_variables"),
        description="Environment variables injected into the container (dict[str, str] wire form).",
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

    # Pod scheduling priority
    priority_class_name: Optional[str] = Field(
        None, description="``priorityClassName`` applied to pods."
    )

    # Pod termination
    termination_grace_period_seconds: Optional[int] = Field(
        None, description="``terminationGracePeriodSeconds`` applied to pods."
    )

    # Container health probes
    readiness_probe: Optional[dict[str, Any]] = Field(
        None, description="Readiness probe applied to the container."
    )
    liveness_probe: Optional[dict[str, Any]] = Field(
        None, description="Liveness probe applied to the container."
    )

    # Pod-level security context
    security_context: Optional[dict[str, Any]] = Field(
        None, description="Pod-level ``securityContext`` (``V1PodSecurityContext``)."
    )

    # Job lifecycle
    ttl_seconds_after_finished: Optional[int] = Field(
        None,
        description=(
            "``ttlSecondsAfterFinished`` for the Job handler — the Job is cleaned "
            "up automatically after this many seconds once it completes."
        ),
    )
    active_deadline_seconds: Optional[int] = Field(
        None,
        description=(
            "``activeDeadlineSeconds`` for the Job handler — the Job is terminated "
            "if it runs longer than this many seconds."
        ),
    )

    # Raw partial override applied AFTER the computed pod spec is built.
    pod_spec_override: Optional[dict[str, Any]] = Field(
        None,
        description=(
            "Partial pod-spec override deep-merged onto the computed pod spec at acquire time."
        ),
    )

    # Full native kubernetes API body — escape hatch.
    native_spec: Optional[dict[str, Any]] = Field(
        None,
        description=(
            "Full native kubernetes API body passed straight to the SDK when "
            "the provider's native-spec escape hatch is enabled."
        ),
    )

    @field_validator("env", mode="before")
    @classmethod
    def _coerce_env_wire_form(cls, v: Any) -> Optional[dict[str, str]]:
        """Accept dict[str, str] (canonical wire form) or list[dict] (domain dump).

        When ``from_domain`` serialises a ``K8sTemplate`` to a flat dict
        and hands it to this class, ``env`` arrives as the domain's
        ``list[K8sEnvVar-dict]`` form (``[{"name": "K", "value": "V", ...}]``).
        Only entries with a plain ``value`` string are round-tripped; entries
        with ``valueFrom`` are silently dropped because the dict wire form
        cannot express them.
        """
        if v is None:
            return None
        if isinstance(v, dict):
            return {str(k): str(val) for k, val in v.items()}
        if isinstance(v, list):
            out: dict[str, str] = {}
            for entry in v:
                if isinstance(entry, dict):
                    name = entry.get("name")
                    value = entry.get("value")
                    # Only capture simple value entries; valueFrom entries are
                    # not representable in the dict wire form.
                    if name is not None and value is not None:
                        out[str(name)] = str(value)
                elif hasattr(entry, "name") and hasattr(entry, "value"):
                    # K8sEnvVar model instance (rare but handle gracefully).
                    if entry.name is not None and entry.value is not None:
                        out[str(entry.name)] = str(entry.value)
            return out or None
        return v  # let Pydantic's own type checker reject anything else

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
