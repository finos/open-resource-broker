"""Pod-spec construction helpers.

Builds ``kubernetes.client.V1Pod`` objects from ORB templates and request
metadata.  Lives under ``providers/k8s/`` so that the kubernetes
SDK imports stay confined to the provider tree (enforced by the
``test_k8s_leak_detection`` architecture test).

The pod-spec construction reads from the strongly-typed
:class:`K8sTemplate` aggregate.  Generic fields (image, labels, max
replicas) come from the parent :class:`Template`; kubernetes-specific
fields (namespace, resource requests, tolerations, ...) come from the
flat :class:`K8sTemplate` attributes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template import (
    K8sProbe,
    K8sSecurityContext,
    K8sTemplate,
    upcast_to_k8s_template,
)

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import V1Pod


# ---------------------------------------------------------------------------
# Label / name helpers
# ---------------------------------------------------------------------------

# Default DNS-subdomain prefix.  Mirrors ``K8sProviderConfig.label_prefix``
# so callers that do not pass a config can still get sensible labels for tests.
_DEFAULT_LABEL_PREFIX = "orb.io"

# Legacy label key emitted alongside the modern labels when
# ``emit_legacy_labels=True``.  Matches the symphony plugin's request-id label.
LEGACY_REQUEST_ID_LABEL = "symphony/open-resource-broker-reqid"

# Maximum length of a pod name segment we will accept.  K8s allows up to 63
# characters total for the metadata.name field (DNS-1123 label); we use
# ``orb-{uuid_no_hyphens[:20]}-{seq:04d}`` which is 31 chars and fits
# comfortably inside the 63-char budget.
_POD_NAME_MAX_LEN = 63


def make_pod_name(request_id: str, seq: int) -> str:
    """Build a deterministic pod name for a single ORB unit.

    Pattern: ``orb-{uuid_no_hyphens[:20]}-{seq:04d}`` where
    ``uuid_no_hyphens`` is the request_id with hyphens stripped.  Taking
    20 hex digits gives ~2^80 distinct prefixes — a negligible collision
    probability even with thousands of concurrent requests sharing the same
    namespace.  The resulting name is 31 chars, well under the 63-char
    DNS-1123 label limit.
    """
    safe = (request_id or "unknown").replace("-", "")
    prefix = safe[:20] if safe else "unknown"
    name = f"orb-{prefix}-{seq:04d}"
    if len(name) > _POD_NAME_MAX_LEN:  # pragma: no cover — defensive
        name = name[:_POD_NAME_MAX_LEN]
    return name


def build_pod_labels(
    request: Request,
    *,
    machine_id: str,
    provider_api: str = "Pod",
    label_prefix: str = _DEFAULT_LABEL_PREFIX,
    emit_legacy_labels: bool = True,
    extra_labels: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Construct the label map applied to every managed pod.

    Operator-supplied ``extra_labels`` (typically derived from
    ``Template.tags``) are merged in first; ORB-system label keys then
    overwrite any conflicts so the request-id / machine-id / managed
    sentinels are always present.
    """
    labels: dict[str, str] = {}
    if extra_labels:
        labels.update({str(k): str(v) for k, v in extra_labels.items()})
    labels.update(
        {
            f"{label_prefix}/managed": "true",
            f"{label_prefix}/request-id": str(request.request_id),
            f"{label_prefix}/machine-id": machine_id,
            f"{label_prefix}/provider-api": provider_api,
            f"{label_prefix}/template-id": str(request.template_id),
        }
    )
    if emit_legacy_labels:
        labels[LEGACY_REQUEST_ID_LABEL] = str(request.request_id)
    return labels


def request_id_label_selector(
    request: Request,
    *,
    label_prefix: str = _DEFAULT_LABEL_PREFIX,
) -> str:
    """Build the ``label_selector=orb.io/request-id=<id>`` string."""
    return f"{label_prefix}/request-id={request.request_id}"


# ---------------------------------------------------------------------------
# Shared helpers — typed-template field projection
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` onto ``base`` (override wins on leaves).

    Nested dicts merge recursively; lists / scalars replace wholesale.
    ``base`` is not mutated.
    """
    out: dict[str, Any] = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def apply_pod_spec_override(pod: "V1Pod", override: Optional[dict[str, Any]]) -> "V1Pod":
    """Deep-merge ``override`` onto the pod's ``spec`` payload.

    The ``restartPolicy: Never`` invariant is mandatory: ORB relies on pods
    not self-restarting so that a pod deletion is always a clean release.
    Any override that tries to change this is rejected before and after the
    merge so the error message points at the offending key.
    """
    if not override:
        return pod
    from kubernetes.client import V1PodSpec  # noqa: PLC0415

    from orb.providers.k8s.exceptions.k8s_errors import K8sError  # noqa: PLC0415

    if pod.spec is None:  # pragma: no cover — defensive
        return pod

    # Early check on both snake_case and camelCase keys before the merge so
    # the error points directly at the override the operator supplied.
    _RESTART_OVERRIDE_KEYS = ("restart_policy", "restartPolicy")
    for key in _RESTART_OVERRIDE_KEYS:
        if key in override and override[key] != "Never":
            raise K8sError(
                f"pod_spec_override contains '{key}: {override[key]!r}' which would "
                "overwrite the mandatory restartPolicy=Never invariant. "
                "ORB requires restartPolicy=Never so that pod deletion is always a "
                "clean release. Remove the offending key from pod_spec_override."
            )

    # Normalise operator-supplied override: the kubernetes Python SDK uses
    # snake_case constructor arguments, so any camelCase key that slipped in
    # must be converted before we pass the merged dict to V1PodSpec().
    # We do this via the shared _normalise_sdk_kwargs helper (top-level keys
    # only for the spec dict; nested dicts are handled by the helper itself).
    normalised_override = _normalise_sdk_kwargs(override)

    raw_spec: Any = pod.spec.to_dict() if hasattr(pod.spec, "to_dict") else pod.spec
    spec_dict: dict[str, Any] = dict(raw_spec) if raw_spec else {}
    merged = _deep_merge(spec_dict, normalised_override)
    pod.spec = V1PodSpec(**merged)

    # Post-merge assertion: the deep-merge must not have silently clobbered
    # restart_policy through a nested path we did not anticipate.
    if pod.spec.restart_policy != "Never":
        raise K8sError(
            f"pod_spec_override silently changed restartPolicy to "
            f"{pod.spec.restart_policy!r} after deep-merge. "
            "The mandatory restartPolicy=Never invariant must be preserved."
        )

    return pod


def build_container_resources(k8s_template: K8sTemplate) -> Optional[Any]:
    """Build ``V1ResourceRequirements`` from the typed template fields."""
    requests = k8s_template.resolve_resource_requests_map()
    limits = k8s_template.resolve_resource_limits_map()
    if not requests and not limits:
        return None
    from kubernetes.client import V1ResourceRequirements  # noqa: PLC0415

    return V1ResourceRequirements(requests=requests, limits=limits)


def build_container_env(k8s_template: K8sTemplate) -> Optional[list[Any]]:
    """Build the ``V1EnvVar`` list from the typed env field."""
    api_list = k8s_template.resolve_env_api_list()
    if not api_list:
        return None
    from kubernetes.client import V1EnvVar  # noqa: PLC0415

    return [V1EnvVar(**entry) for entry in api_list]


def build_pod_tolerations(
    k8s_template: K8sTemplate,
    *,
    config: Optional[K8sProviderConfig],
) -> Optional[list[Any]]:
    """Resolve tolerations from template (preferred) or provider-config defaults."""
    from kubernetes.client import V1Toleration  # noqa: PLC0415

    api_list = k8s_template.resolve_tolerations_api_list()
    if api_list:
        return [V1Toleration(**entry) for entry in api_list]
    if config is not None and config.default_tolerations:
        return [V1Toleration(**dict(t)) for t in config.default_tolerations]
    return None


def build_pod_volumes(k8s_template: K8sTemplate) -> Optional[list[Any]]:
    """Build the ``V1Volume`` list from the typed volumes field."""
    api_list = k8s_template.resolve_volumes_api_list()
    if not api_list:
        return None
    from kubernetes.client import V1Volume  # noqa: PLC0415

    out: list[V1Volume] = []
    for entry in api_list:
        try:
            out.append(V1Volume(**entry))
        except (TypeError, ValueError):
            out.append(V1Volume(name=entry.get("name", "unnamed")))
    return out


def _camel_to_snake(name: str) -> str:
    """Convert a camelCase key to snake_case for the kubernetes SDK constructors.

    The kubernetes Python SDK exposes snake_case constructor arguments
    (e.g. ``mount_path``, ``run_as_user``) even though the wire format uses
    camelCase.  This helper normalises operator-supplied camelCase dict keys
    into the shape the SDK expects.
    """
    import re  # noqa: PLC0415

    # Insert underscores before uppercase letters, then lower-case the result.
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def _normalise_sdk_kwargs(d: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *d* with camelCase keys converted to snake_case.

    Nested dicts (but not lists) are also normalised recursively.
    """
    out: dict[str, Any] = {}
    for key, value in d.items():
        snake_key = _camel_to_snake(key)
        if isinstance(value, dict):
            out[snake_key] = _normalise_sdk_kwargs(value)
        else:
            out[snake_key] = value
    return out


def build_container_volume_mounts(k8s_template: K8sTemplate) -> Optional[list[Any]]:
    """Build the ``V1VolumeMount`` list from the typed volume_mounts field.

    Operator-supplied entries may use camelCase keys (e.g. ``mountPath``)
    matching the kubernetes JSON API surface.  They are normalised to
    the snake_case names that the Python SDK's constructor expects.
    """
    if not k8s_template.volume_mounts:
        return None
    from kubernetes.client import V1VolumeMount  # noqa: PLC0415

    out: list[V1VolumeMount] = []
    for entry in k8s_template.volume_mounts:
        if isinstance(entry, dict):
            kwargs = _normalise_sdk_kwargs(entry)
            try:
                out.append(V1VolumeMount(**kwargs))
            except (TypeError, ValueError):
                # Fall back to mount_path-less stub — callers still get
                # a mount entry rather than silently dropping it.
                out.append(
                    V1VolumeMount(
                        name=kwargs.get("name", "unnamed"),
                        mount_path=kwargs.get("mount_path", "/"),
                    )
                )
        else:
            out.append(entry)  # type: ignore[arg-type]
    return out or None


def build_container_probe(probe: Optional["K8sProbe"]) -> Optional[Any]:
    """Build a ``V1Probe`` from a :class:`K8sProbe` domain object.

    The domain model uses camelCase aliases for the JSON surface; the SDK
    constructor expects snake_case names.  ``model_dump(exclude_none=True)``
    (no alias) produces the correct snake_case keys, with one exception:
    the ``exec`` field is mapped to ``_exec`` because ``exec`` is a Python
    keyword and the SDK uses the leading-underscore convention.
    """
    if probe is None:
        return None
    from kubernetes.client import V1Probe  # noqa: PLC0415

    # model_dump without by_alias gives snake_case field names.
    kwargs = probe.model_dump(exclude_none=True)
    # ``exec`` is a Python reserved word; the SDK constructor uses ``_exec``.
    if "exec" in kwargs:
        kwargs["_exec"] = kwargs.pop("exec")
    return V1Probe(**kwargs)


def build_pod_security_context(
    security_context: Optional["K8sSecurityContext"],
) -> Optional[Any]:
    """Build a ``V1PodSecurityContext`` from a :class:`K8sSecurityContext` domain object.

    Uses snake_case ``model_dump`` to match the SDK constructor signature.
    """
    if security_context is None:
        return None
    from kubernetes.client import V1PodSecurityContext  # noqa: PLC0415

    kwargs = security_context.model_dump(exclude_none=True)
    return V1PodSecurityContext(**kwargs)


def resolve_node_selector(
    k8s_template: K8sTemplate,
    *,
    config: Optional[K8sProviderConfig],
) -> Optional[dict[str, str]]:
    """Resolve ``nodeSelector`` from template (preferred) or provider config."""
    if k8s_template.node_selector:
        return dict(k8s_template.node_selector)
    if config is not None and config.default_node_selector:
        return dict(config.default_node_selector)
    return None


def resolve_image_pull_secret_name(
    k8s_template: K8sTemplate,
    *,
    config: Optional[K8sProviderConfig],
) -> Optional[str]:
    """Resolve ``imagePullSecrets[0].name`` from template or provider config."""
    if k8s_template.image_pull_secret:
        return str(k8s_template.image_pull_secret)
    if config is not None and config.default_image_pull_secret:
        return str(config.default_image_pull_secret)
    return None


# ---------------------------------------------------------------------------
# Pod-spec assembly
# ---------------------------------------------------------------------------


def build_pod_spec(
    template: Template,
    request: Request,
    *,
    pod_name: str,
    machine_id: str,
    namespace: str,
    provider_api: str = "Pod",
    config: Optional[K8sProviderConfig] = None,
) -> "V1Pod":
    """Build a single ``V1Pod`` for the supplied template and request.

    Mandatory invariants:

    * ``restartPolicy: Never`` is always set — ORB controls retry semantics
      and a self-restarting container would defeat per-pod release.
    * Labels include ``orb.io/managed=true`` and ``orb.io/request-id``;
      callers can filter by these to scope list operations.
    """
    # Lazy SDK import keeps callers without the ``[kubernetes]`` extra
    # able to import this module.
    from kubernetes.client import (  # noqa: PLC0415
        V1Container,
        V1LocalObjectReference,
        V1ObjectMeta,
        V1Pod,
        V1PodSpec,
    )

    k8s_template = upcast_to_k8s_template(template)

    label_prefix = config.label_prefix if config is not None else _DEFAULT_LABEL_PREFIX
    emit_legacy_labels = config.emit_legacy_labels if config is not None else True

    operator_labels = k8s_template.resolve_pod_labels()
    labels = build_pod_labels(
        request,
        machine_id=machine_id,
        provider_api=provider_api,
        label_prefix=label_prefix,
        emit_legacy_labels=emit_legacy_labels,
        extra_labels=operator_labels,
    )

    image = k8s_template.resolve_container_image()
    resources = build_container_resources(k8s_template)
    env = build_container_env(k8s_template)
    volume_mounts = build_container_volume_mounts(k8s_template)
    readiness_probe = build_container_probe(k8s_template.readiness_probe)
    liveness_probe = build_container_probe(k8s_template.liveness_probe)

    container = V1Container(
        name="orb",
        image=image,
        command=k8s_template.command,
        args=k8s_template.args,
        resources=resources,
        env=env,
        volume_mounts=volume_mounts,
        readiness_probe=readiness_probe,
        liveness_probe=liveness_probe,
    )

    node_selector = resolve_node_selector(k8s_template, config=config)
    tolerations = build_pod_tolerations(k8s_template, config=config)
    pull_secret_name = resolve_image_pull_secret_name(k8s_template, config=config)
    image_pull_secrets = (
        [V1LocalObjectReference(name=pull_secret_name)] if pull_secret_name else None
    )
    volumes = build_pod_volumes(k8s_template)
    security_context = build_pod_security_context(k8s_template.security_context)

    pod_spec_kwargs: dict[str, Any] = {
        "containers": [container],
        "restart_policy": "Never",
    }
    if node_selector is not None:
        pod_spec_kwargs["node_selector"] = node_selector
    if tolerations is not None:
        pod_spec_kwargs["tolerations"] = tolerations
    if image_pull_secrets is not None:
        pod_spec_kwargs["image_pull_secrets"] = image_pull_secrets
    if volumes is not None:
        pod_spec_kwargs["volumes"] = volumes
    if k8s_template.service_account:
        pod_spec_kwargs["service_account_name"] = k8s_template.service_account
    if k8s_template.runtime_class:
        pod_spec_kwargs["runtime_class_name"] = k8s_template.runtime_class
    if k8s_template.priority_class_name:
        pod_spec_kwargs["priority_class_name"] = k8s_template.priority_class_name
    if k8s_template.termination_grace_period_seconds is not None:
        pod_spec_kwargs["termination_grace_period_seconds"] = (
            k8s_template.termination_grace_period_seconds
        )
    if security_context is not None:
        pod_spec_kwargs["security_context"] = security_context

    pod_metadata = V1ObjectMeta(
        name=pod_name,
        namespace=namespace,
        labels=labels,
        annotations=(dict(k8s_template.annotations) if k8s_template.annotations else None),
    )

    pod = V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=pod_metadata,
        spec=V1PodSpec(**pod_spec_kwargs),
    )
    return apply_pod_spec_override(pod, k8s_template.pod_spec_override)


__all__ = [
    "LEGACY_REQUEST_ID_LABEL",
    "apply_pod_spec_override",
    "build_container_env",
    "build_container_probe",
    "build_container_resources",
    "build_container_volume_mounts",
    "build_pod_labels",
    "build_pod_security_context",
    "build_pod_spec",
    "build_pod_tolerations",
    "build_pod_volumes",
    "make_pod_name",
    "request_id_label_selector",
    "resolve_image_pull_secret_name",
    "resolve_node_selector",
]
