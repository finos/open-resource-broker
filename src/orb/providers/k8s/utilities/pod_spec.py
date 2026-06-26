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
# ``orb-{request_id[:8]}-{seq:04d}`` which is 17 chars and fits comfortably.
_POD_NAME_MAX_LEN = 63


def make_pod_name(request_id: str, seq: int) -> str:
    """Build a deterministic pod name for a single ORB unit.

    Pattern: ``orb-{request_id[:8]}-{seq:04d}``.  Stays under the 63-char
    DNS-1123 label limit and is human-readable.
    """
    prefix = (request_id or "unknown")[:8]
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
    """Deep-merge ``override`` onto the pod's ``spec`` payload."""
    if not override:
        return pod
    from kubernetes.client import V1PodSpec  # noqa: PLC0415

    if pod.spec is None:  # pragma: no cover — defensive
        return pod
    spec_dict = pod.spec.to_dict() if hasattr(pod.spec, "to_dict") else dict(pod.spec)
    merged = _deep_merge(spec_dict, override)
    pod.spec = V1PodSpec(**merged)
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

    container = V1Container(
        name="orb",
        image=image,
        command=k8s_template.command,
        args=k8s_template.args,
        resources=resources,
        env=env,
    )

    node_selector = resolve_node_selector(k8s_template, config=config)
    tolerations = build_pod_tolerations(k8s_template, config=config)
    pull_secret_name = resolve_image_pull_secret_name(k8s_template, config=config)
    image_pull_secrets = (
        [V1LocalObjectReference(name=pull_secret_name)] if pull_secret_name else None
    )
    volumes = build_pod_volumes(k8s_template)

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
    "build_container_resources",
    "build_pod_labels",
    "build_pod_spec",
    "build_pod_tolerations",
    "build_pod_volumes",
    "make_pod_name",
    "request_id_label_selector",
    "resolve_image_pull_secret_name",
    "resolve_node_selector",
]
