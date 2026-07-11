"""Kubernetes-specific template domain extension.

Mirrors :class:`orb.providers.aws.domain.template.aws_template_aggregate.AWSTemplate`
for the kubernetes provider.  ``K8sTemplate`` is a strongly-typed
``Template`` subclass: kubernetes-specific operator-supplied fields live as
flat first-class attributes rather than as opaque entries under
``Template.provider_data['k8s']``.

Generic provisioning concepts continue to be expressed on the parent
``Template``:

* ``image_id``         — container image string consumed by the spec builders.
* ``tags``             — operator tags; merged into the pod ``metadata.labels``
  surface at spec-build time.
* ``max_instances``    — quota cap; the per-request replica count comes from
  ``request.requested_count``.
* ``instance_profile`` — falls back as the ``serviceAccountName`` when
  :attr:`K8sTemplate.service_account` is not set.

Upcasting an arbitrary ``Template`` to a ``K8sTemplate`` is safe via
``K8sTemplate.model_validate(template.model_dump())`` because every field
on the parent type is preserved and every k8s-specific field is
``Optional``.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orb.domain.template.template_aggregate import Template

# Docker image name must start with an alphanumeric character and must not
# contain whitespace.  Spaces cause kubelet to report InvalidImageName.
_DOCKER_IMAGE_NAME_RE = re.compile(r"^[a-zA-Z0-9][^\s]*$")

# ---------------------------------------------------------------------------
# Supporting value-object models
# ---------------------------------------------------------------------------


class K8sToleration(BaseModel):
    """Kubernetes pod ``Toleration`` payload.

    Field names match the kubernetes API surface so the model serialises
    cleanly into the ``V1Toleration`` constructor without further mapping.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    key: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[str] = None
    effect: Optional[str] = None
    toleration_seconds: Optional[int] = Field(default=None, alias="tolerationSeconds")

    def to_api_dict(self) -> dict[str, Any]:
        """Serialise to the dict shape accepted by the kubernetes SDK.

        The SDK expects snake_case (V1Toleration accepts ``toleration_seconds``,
        not the alias ``tolerationSeconds``), so we deliberately disable
        ``by_alias`` here.
        """
        return self.model_dump(by_alias=False, exclude_none=True)


class K8sResourceQuantities(BaseModel):
    """Kubernetes container ``resources.requests`` / ``resources.limits`` payload.

    All quantities are strings (e.g. ``"500m"``, ``"1Gi"``) — the
    kubernetes API server is responsible for parsing them, so we treat the
    values as opaque tokens at the domain boundary.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    cpu: Optional[str] = None
    memory: Optional[str] = None
    ephemeral_storage: Optional[str] = Field(default=None, alias="ephemeralStorage")

    # Optional accelerator entries.  When set these are surfaced as
    # ``<gpu_type>: <gpu_count>`` entries under the same resources map so
    # operators can request GPUs without a separate Pydantic model.
    gpu_count: Optional[int] = Field(default=None, alias="gpuCount")
    gpu_type: Optional[str] = Field(default=None, alias="gpuType")

    def to_resource_map(self) -> dict[str, str]:
        """Return the flat ``resource -> quantity`` dict the SDK expects.

        Keys without an explicit value are omitted.  GPU entries are
        emitted as ``<gpu_type>: <gpu_count>`` only when both fields are
        present.
        """
        out: dict[str, str] = {}
        if self.cpu:
            out["cpu"] = str(self.cpu)
        if self.memory:
            out["memory"] = str(self.memory)
        if self.ephemeral_storage:
            out["ephemeral-storage"] = str(self.ephemeral_storage)
        if self.gpu_type and self.gpu_count is not None:
            out[self.gpu_type] = str(self.gpu_count)
        return out


class K8sEnvVarSource(BaseModel):
    """Subset of ``EnvVarSource`` supported by the typed env-var payload."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    config_map_key_ref: Optional[dict[str, Any]] = Field(default=None, alias="configMapKeyRef")
    secret_key_ref: Optional[dict[str, Any]] = Field(default=None, alias="secretKeyRef")
    field_ref: Optional[dict[str, Any]] = Field(default=None, alias="fieldRef")
    resource_field_ref: Optional[dict[str, Any]] = Field(default=None, alias="resourceFieldRef")

    def to_api_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class K8sEnvVar(BaseModel):
    """Kubernetes container ``EnvVar`` payload (``name`` + ``value`` or ``valueFrom``)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str
    value: Optional[str] = None
    value_from: Optional[K8sEnvVarSource] = Field(default=None, alias="valueFrom")

    @model_validator(mode="after")
    def _validate_value_or_value_from(self) -> K8sEnvVar:
        if self.value is not None and self.value_from is not None:
            raise ValueError("K8sEnvVar accepts either 'value' or 'value_from', not both")
        return self

    def to_api_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.value is not None:
            out["value"] = self.value
        if self.value_from is not None:
            out["valueFrom"] = self.value_from.to_api_dict()
        return out


class K8sVolume(BaseModel):
    """Kubernetes pod-level ``Volume`` payload.

    The volume source (``configMap`` / ``secret`` / ``emptyDir`` / ...) is
    kept as an opaque dict so we do not have to model every supported
    volume kind at the domain layer.  The dict is passed straight through
    to the SDK at spec-build time.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str
    source: dict[str, Any] = Field(default_factory=dict)

    def to_api_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        out.update(self.source)
        return out


class K8sProbe(BaseModel):
    """Kubernetes container probe (readiness / liveness).

    Covers the most common probe mechanisms.  Opaque fields not modelled
    here can be reached via ``pod_spec_override``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # HTTP GET probe
    http_get: Optional[dict[str, Any]] = Field(default=None, alias="httpGet")
    # Exec probe
    exec: Optional[dict[str, Any]] = None
    # TCP socket probe
    tcp_socket: Optional[dict[str, Any]] = Field(default=None, alias="tcpSocket")
    # Timing knobs
    initial_delay_seconds: Optional[int] = Field(default=None, alias="initialDelaySeconds")
    period_seconds: Optional[int] = Field(default=None, alias="periodSeconds")
    timeout_seconds: Optional[int] = Field(default=None, alias="timeoutSeconds")
    success_threshold: Optional[int] = Field(default=None, alias="successThreshold")
    failure_threshold: Optional[int] = Field(default=None, alias="failureThreshold")

    def to_api_dict(self) -> dict[str, Any]:
        """Serialise to the dict shape accepted by the kubernetes SDK."""
        return self.model_dump(by_alias=True, exclude_none=True)


class K8sSecurityContext(BaseModel):
    """Pod-level ``securityContext`` (``V1PodSecurityContext``).

    Covers the fields required for non-root UID/GID, seccomp, and
    filesystem group assignments.  Less common fields can be reached via
    ``pod_spec_override``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    run_as_user: Optional[int] = Field(default=None, alias="runAsUser")
    run_as_group: Optional[int] = Field(default=None, alias="runAsGroup")
    fs_group: Optional[int] = Field(default=None, alias="fsGroup")
    run_as_non_root: Optional[bool] = Field(default=None, alias="runAsNonRoot")
    seccomp_profile: Optional[dict[str, Any]] = Field(default=None, alias="seccompProfile")

    def to_api_dict(self) -> dict[str, Any]:
        """Serialise to the dict shape accepted by the kubernetes SDK."""
        return self.model_dump(by_alias=True, exclude_none=True)


# ---------------------------------------------------------------------------
# K8sTemplate aggregate
# ---------------------------------------------------------------------------


def _coerce_tolerations(
    value: Any,
) -> Optional[list[K8sToleration]]:
    """Accept dict / model / list-of-either inputs for the tolerations field."""
    if value is None:
        return None
    if isinstance(value, K8sToleration):
        return [value]
    if isinstance(value, dict):
        return [K8sToleration.model_validate(value)]
    if isinstance(value, list):
        out: list[K8sToleration] = []
        for entry in value:
            if isinstance(entry, K8sToleration):
                out.append(entry)
            elif isinstance(entry, dict):
                out.append(K8sToleration.model_validate(entry))
            else:
                raise TypeError(f"Unsupported toleration entry type: {type(entry).__name__}")
        return out
    raise TypeError(f"Unsupported tolerations payload type: {type(value).__name__}")


def _coerce_resource_quantities(
    value: Any,
) -> Optional[K8sResourceQuantities]:
    """Accept dict / model inputs for resource_requests / resource_limits."""
    if value is None:
        return None
    if isinstance(value, K8sResourceQuantities):
        return value
    if isinstance(value, dict):
        return K8sResourceQuantities.model_validate(value)
    raise TypeError(f"Unsupported resource-quantities payload type: {type(value).__name__}")


def _coerce_env(value: Any) -> Optional[list[K8sEnvVar]]:
    """Accept list[K8sEnvVar] / list[dict] / dict[str, str] for env vars.

    The dict-of-strings shape is accepted for compatibility with operators
    who set ``env`` as ``{"FOO": "bar"}``; it is normalised to the typed
    list shape.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return [K8sEnvVar(name=str(k), value=str(v)) for k, v in value.items()]
    if isinstance(value, list):
        out: list[K8sEnvVar] = []
        for entry in value:
            if isinstance(entry, K8sEnvVar):
                out.append(entry)
            elif isinstance(entry, dict):
                out.append(K8sEnvVar.model_validate(entry))
            else:
                raise TypeError(f"Unsupported env entry type: {type(entry).__name__}")
        return out
    raise TypeError(f"Unsupported env payload type: {type(value).__name__}")


def _coerce_probe(value: Any) -> Optional[K8sProbe]:
    """Accept dict / model inputs for probe fields."""
    if value is None:
        return None
    if isinstance(value, K8sProbe):
        return value
    if isinstance(value, dict):
        return K8sProbe.model_validate(value)
    raise TypeError(f"Unsupported probe payload type: {type(value).__name__}")


def _coerce_security_context(value: Any) -> Optional[K8sSecurityContext]:
    """Accept dict / model inputs for the security_context field."""
    if value is None:
        return None
    if isinstance(value, K8sSecurityContext):
        return value
    if isinstance(value, dict):
        return K8sSecurityContext.model_validate(value)
    raise TypeError(f"Unsupported security_context payload type: {type(value).__name__}")


def _coerce_volumes(value: Any) -> Optional[list[K8sVolume]]:
    """Accept list[K8sVolume] / list[dict] for the volumes field."""
    if value is None:
        return None
    if isinstance(value, list):
        out: list[K8sVolume] = []
        for entry in value:
            if isinstance(entry, K8sVolume):
                out.append(entry)
            elif isinstance(entry, dict):
                if "source" in entry:
                    out.append(K8sVolume.model_validate(entry))
                else:
                    # Treat the remaining keys as the source dict; common
                    # k8s shape is ``{"name": "data", "configMap": {...}}``.
                    name = entry.get("name")
                    if not isinstance(name, str) or not name:
                        raise ValueError("volume entry requires a non-empty 'name'")
                    source = {k: v for k, v in entry.items() if k != "name"}
                    out.append(K8sVolume(name=name, source=source))
            else:
                raise TypeError(f"Unsupported volume entry type: {type(entry).__name__}")
        return out
    raise TypeError(f"Unsupported volumes payload type: {type(value).__name__}")


class K8sTemplate(Template):
    """Kubernetes-specific template aggregate.

    Operator-supplied kubernetes fields are first-class attributes on this
    class.  The legacy ``provider_data['k8s']`` nested-dict surface is
    accepted at construction time as a promotion source so existing
    operator configs continue to round-trip, but new callers should set
    the typed fields directly.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        # Inherit ``validate_assignment`` from the parent so mutating
        # attributes post-construction still runs the validators.
    )

    # Typed provider-config forwarded from TemplateDTO (round-trip via model_dump).
    # Accepts a dict (serialised K8sTemplateDTOConfig) or None; values are
    # promoted to their respective fields in :meth:`_promote_extensions`.
    provider_config: Optional[dict[str, Any]] = None

    # Scheduling / placement
    namespace: Optional[str] = None
    namespaces: Optional[list[str]] = None
    node_selector: Optional[dict[str, str]] = None
    tolerations: Optional[list[K8sToleration]] = None
    runtime_class: Optional[str] = None

    # Container image pull / runtime
    image_pull_secret: Optional[str] = None

    # Container resources
    resource_requests: Optional[K8sResourceQuantities] = None
    resource_limits: Optional[K8sResourceQuantities] = None

    # Container environment / mounts / volumes
    env: Optional[list[K8sEnvVar]] = None
    volume_mounts: Optional[list[dict[str, Any]]] = None
    volumes: Optional[list[K8sVolume]] = None

    # Container command override
    command: Optional[list[str]] = None
    args: Optional[list[str]] = None

    # Pod metadata
    annotations: Optional[dict[str, str]] = None

    # Workload sizing overrides (Job-specific; replica count comes from
    # ``request.requested_count`` and ``max_instances`` caps the quota).
    completions: Optional[int] = None
    parallelism: Optional[int] = None

    # Identity overrides
    service_account: Optional[str] = None

    # Pod scheduling priority
    priority_class_name: Optional[str] = None

    # Pod termination
    termination_grace_period_seconds: Optional[int] = None

    # Container health probes
    readiness_probe: Optional[K8sProbe] = None
    liveness_probe: Optional[K8sProbe] = None

    # Pod-level security context (V1PodSecurityContext)
    security_context: Optional[K8sSecurityContext] = None

    # Job lifecycle knobs
    ttl_seconds_after_finished: Optional[int] = None
    active_deadline_seconds: Optional[int] = None

    # Raw partial override applied AFTER the computed pod spec is built.
    # Distinct from :attr:`native_spec` below — ``pod_spec_override`` is a
    # partial deep-merge onto the computed pod spec built by the spec
    # helpers, whereas ``native_spec`` is a full-replacement escape hatch
    # that bypasses the helpers entirely.
    pod_spec_override: Optional[dict[str, Any]] = None

    # Full native kubernetes API body for the per-handler create call.
    # When set (and the provider config enables native specs), the handler
    # passes the rendered dict directly to the kubernetes SDK
    # (e.g. ``create_namespaced_pod(body=native_spec)``) instead of
    # building the body via :mod:`orb.providers.k8s.utilities.pod_spec`
    # and friends.  The dict is Jinja-templated against the standard
    # template context at acquire time, then deep-merged onto the
    # provider's per-API default Jinja template so operators can supply a
    # partial override (e.g. only ``spec.containers[0].resources``) and
    # have the rest come from the default.  Mirrors the AWS provider's
    # ``provider_api_spec`` field — see
    # :class:`orb.providers.k8s.infrastructure.services.k8s_native_spec_service.K8sNativeSpecService`.
    native_spec: Optional[dict[str, Any]] = None

    def __init__(self, **data: Any) -> None:
        """Initialise the K8sTemplate.

        ``provider_type`` is forced to ``"k8s"`` so the template factory and
        provider-resolver code paths route the template through the
        kubernetes provider without callers having to set it explicitly.
        """
        data.setdefault("provider_type", "k8s")
        super().__init__(**data)

    # ------------------------------------------------------------------
    # Field coercion validators
    # ------------------------------------------------------------------

    @field_validator("image_id")
    @classmethod
    def _validate_image_id(cls, value: Optional[str]) -> Optional[str]:
        """Reject image names that contain whitespace (causes kubelet InvalidImageName)."""
        if value is None or not str(value).strip():
            return value
        if not _DOCKER_IMAGE_NAME_RE.match(str(value)):
            raise ValueError(
                f"Invalid container image name: {value!r}. "
                "Image names must start with an alphanumeric character and "
                "must not contain whitespace."
            )
        return value

    @field_validator("tolerations", mode="before")
    @classmethod
    def _coerce_tolerations_input(cls, value: Any) -> Optional[list[K8sToleration]]:
        return _coerce_tolerations(value)

    @field_validator("resource_requests", "resource_limits", mode="before")
    @classmethod
    def _coerce_resource_quantities_input(cls, value: Any) -> Optional[K8sResourceQuantities]:
        return _coerce_resource_quantities(value)

    @field_validator("env", mode="before")
    @classmethod
    def _coerce_env_input(cls, value: Any) -> Optional[list[K8sEnvVar]]:
        return _coerce_env(value)

    @field_validator("volumes", mode="before")
    @classmethod
    def _coerce_volumes_input(cls, value: Any) -> Optional[list[K8sVolume]]:
        return _coerce_volumes(value)

    @field_validator("readiness_probe", "liveness_probe", mode="before")
    @classmethod
    def _coerce_probe_input(cls, value: Any) -> Optional[K8sProbe]:
        return _coerce_probe(value)

    @field_validator("security_context", mode="before")
    @classmethod
    def _coerce_security_context_input(cls, value: Any) -> Optional[K8sSecurityContext]:
        return _coerce_security_context(value)

    @field_validator("termination_grace_period_seconds", "ttl_seconds_after_finished")
    @classmethod
    def _validate_non_negative_seconds(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError("seconds fields must be non-negative integers when set")
        return value

    @field_validator("active_deadline_seconds")
    @classmethod
    def _validate_positive_deadline(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("active_deadline_seconds must be a positive integer when set")
        return value

    @field_validator("namespace")
    @classmethod
    def _validate_namespace(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("namespace must be a non-empty string when set")
        return value

    @field_validator("completions", "parallelism")
    @classmethod
    def _validate_positive_counts(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("completions / parallelism must be positive integers")
        return value

    # ------------------------------------------------------------------
    # Extension-config promotion + service-account fallback
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _promote_extensions_and_defaults(self) -> K8sTemplate:
        """Promote DTO-config dict + apply the service-account fallback.

        Two responsibilities:

        1. When :attr:`provider_config` is a dict (the
           :class:`K8sTemplateDTOConfig` round-trip path), copy its values
           onto the matching typed fields when those are unset.  This keeps
           backwards compatibility with operators who set kubernetes fields
           via the DTO surface rather than via the typed template
           directly.
        2. Fall back to :attr:`Template.instance_profile` for
           :attr:`service_account` when the latter is unset.  This honours
           the documented mapping of the generic ``instance_profile``
           field (see :class:`Template` line 57) onto the kubernetes
           ``serviceAccountName`` concept.
        """
        # Promote provider_config dict entries onto typed fields.
        pc = getattr(self, "provider_config", None)
        if isinstance(pc, dict):
            self._promote_field(pc, "namespace")
            self._promote_field(pc, "runtime_class")
            self._promote_field(pc, "service_account")
            self._promote_field(pc, "node_selector")
            self._promote_field(pc, "image_pull_secret")
            self._promote_field(pc, "annotations")
            self._promote_field(pc, "volume_mounts")
            self._promote_field(pc, "command")
            self._promote_field(pc, "args")
            self._promote_field(pc, "completions")
            self._promote_field(pc, "parallelism")
            self._promote_field(pc, "priority_class_name")
            self._promote_field(pc, "termination_grace_period_seconds")
            self._promote_field(pc, "ttl_seconds_after_finished")
            self._promote_field(pc, "active_deadline_seconds")
            self._promote_field(pc, "pod_spec_override")
            self._promote_field(pc, "native_spec")

            # Coerced probe / security-context fields go through per-field validators.
            if self.readiness_probe is None and pc.get("readiness_probe") is not None:
                object.__setattr__(
                    self, "readiness_probe", _coerce_probe(pc.get("readiness_probe"))
                )
            if self.liveness_probe is None and pc.get("liveness_probe") is not None:
                object.__setattr__(self, "liveness_probe", _coerce_probe(pc.get("liveness_probe")))
            if self.security_context is None and pc.get("security_context") is not None:
                object.__setattr__(
                    self,
                    "security_context",
                    _coerce_security_context(pc.get("security_context")),
                )

            # Coerced fields go through the per-field validator helpers.
            if self.tolerations is None and pc.get("tolerations") is not None:
                object.__setattr__(self, "tolerations", _coerce_tolerations(pc.get("tolerations")))
            if self.resource_requests is None and pc.get("resource_requests") is not None:
                object.__setattr__(
                    self,
                    "resource_requests",
                    _coerce_resource_quantities(pc.get("resource_requests")),
                )
            if self.resource_limits is None and pc.get("resource_limits") is not None:
                object.__setattr__(
                    self,
                    "resource_limits",
                    _coerce_resource_quantities(pc.get("resource_limits")),
                )
            # ``env`` is the canonical key; ``environment_variables`` is still
            # accepted here for any raw provider_config dicts that were
            # serialised before the field rename (belt-and-suspenders).
            if self.env is None:
                env_input = (
                    pc.get("env") if pc.get("env") is not None else pc.get("environment_variables")
                )
                if env_input is not None:
                    object.__setattr__(self, "env", _coerce_env(env_input))
            if self.volumes is None and pc.get("volumes") is not None:
                object.__setattr__(self, "volumes", _coerce_volumes(pc.get("volumes")))

        # Service-account fallback to the generic instance_profile.
        if self.service_account is None and self.instance_profile:
            object.__setattr__(self, "service_account", self.instance_profile)

        return self

    def _promote_field(self, src: dict[str, Any], name: str) -> None:
        """Copy ``src[name]`` onto ``self.<name>`` when both are usefully populated."""
        current = getattr(self, name, None)
        if current is not None:
            return
        candidate = src.get(name)
        if candidate is None:
            return
        object.__setattr__(self, name, candidate)

    # ------------------------------------------------------------------
    # Public helpers consumed by the spec builders
    # ------------------------------------------------------------------

    def resolve_container_image(self) -> str:
        """Return the container image string for this template.

        The generic ``Template.image_id`` field is the single source of
        truth — ``K8sTemplate`` does not redefine ``image_id`` and does
        not honour any nested ``provider_data['k8s']['container_image']``
        legacy path.
        """
        image = self.image_id
        if not image:
            raise ValueError("K8sTemplate is missing a container image — set Template.image_id.")
        return str(image)

    def resolve_pod_labels(self) -> dict[str, str]:
        """Project ``Template.tags`` into kubernetes pod labels.

        Kubernetes label values MUST be strings; arbitrary ``tags`` values
        are coerced via ``str(...)`` and ``None`` entries are dropped.
        """
        if not self.tags:
            return {}
        out: dict[str, str] = {}
        for key, value in self.tags.items():
            if value is None:
                continue
            out[str(key)] = str(value)
        return out

    def resolve_resource_requests_map(self) -> Optional[dict[str, str]]:
        """Flat ``resource -> quantity`` dict for ``V1ResourceRequirements.requests``."""
        if self.resource_requests is None:
            return None
        mapping = self.resource_requests.to_resource_map()
        return mapping or None

    def resolve_resource_limits_map(self) -> Optional[dict[str, str]]:
        """Flat ``resource -> quantity`` dict for ``V1ResourceRequirements.limits``."""
        if self.resource_limits is None:
            return None
        mapping = self.resource_limits.to_resource_map()
        return mapping or None

    def resolve_env_api_list(self) -> Optional[list[dict[str, Any]]]:
        """Return env vars as dicts suitable for the kubernetes SDK."""
        if not self.env:
            return None
        return [entry.to_api_dict() for entry in self.env]

    def resolve_tolerations_api_list(self) -> Optional[list[dict[str, Any]]]:
        """Return tolerations as dicts suitable for the kubernetes SDK."""
        if not self.tolerations:
            return None
        return [t.to_api_dict() for t in self.tolerations]

    def resolve_volumes_api_list(self) -> Optional[list[dict[str, Any]]]:
        """Return volumes as dicts suitable for the kubernetes SDK."""
        if not self.volumes:
            return None
        return [v.to_api_dict() for v in self.volumes]


# ---------------------------------------------------------------------------
# Helper: safe upcast from a generic Template
# ---------------------------------------------------------------------------


def upcast_to_k8s_template(template: Template | K8sTemplate) -> K8sTemplate:
    """Return a :class:`K8sTemplate` view of ``template``.

    When ``template`` is already a :class:`K8sTemplate` it is returned
    unchanged.  Otherwise the generic template is round-tripped through
    ``model_dump`` / ``model_validate`` — every parent-field is preserved
    and every k8s-specific field stays at its ``None`` default.
    """
    if isinstance(template, K8sTemplate):
        return template
    return K8sTemplate.model_validate(template.model_dump())


__all__ = [
    "K8sEnvVar",
    "K8sEnvVarSource",
    "K8sProbe",
    "K8sResourceQuantities",
    "K8sSecurityContext",
    "K8sTemplate",
    "K8sToleration",
    "K8sVolume",
    "upcast_to_k8s_template",
]
