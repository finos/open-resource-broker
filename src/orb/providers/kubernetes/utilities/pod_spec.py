"""Pod-spec construction helpers.

Builds ``kubernetes.client.V1Pod`` objects from ORB templates and request
metadata.  Lives under ``providers/kubernetes/`` so that the kubernetes
SDK imports stay confined to the provider tree (enforced by the
``test_kubernetes_leak_detection`` architecture test).

The pod-spec construction is intentionally minimal in Phase B: one
container per pod, image + optional resource requests + optional
node-selector / tolerations / image-pull-secret defaults from the
provider config.  Later phases add richer template merging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import V1Pod


# ---------------------------------------------------------------------------
# Label / name helpers
# ---------------------------------------------------------------------------

# Default DNS-subdomain prefix.  Mirrors ``KubernetesProviderConfig.label_prefix``
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

    Args:
        request_id: ORB request UUID (string).  Only the first 8 chars are
            used so two pods in the same request share a common prefix.
        seq: 0-based sequence number for this pod within the request.

    Returns:
        A DNS-1123 conformant pod name.
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
    provider_api: str = "KubernetesPod",
    label_prefix: str = _DEFAULT_LABEL_PREFIX,
    emit_legacy_labels: bool = True,
) -> dict[str, str]:
    """Construct the label map applied to every managed pod.

    Args:
        request: ORB request the pod belongs to.
        machine_id: Per-pod unique identifier (typically the pod name).
        provider_api: Provider-API key stamped onto every pod for
            reconciler/cleanup matching (defaults to ``"KubernetesPod"``).
        label_prefix: DNS-subdomain prefix for the ORB labels.  Operators
            override this via ``KubernetesProviderConfig.label_prefix``.
        emit_legacy_labels: When ``True`` (default), also emit the legacy
            ``symphony/open-resource-broker-reqid`` label so legacy
            watchers continue to function during the transition.

    Returns:
        A dict of label-key -> value entries suitable for
        ``metadata.labels``.
    """
    labels: dict[str, str] = {
        f"{label_prefix}/managed": "true",
        f"{label_prefix}/request-id": str(request.request_id),
        f"{label_prefix}/machine-id": machine_id,
        f"{label_prefix}/provider-api": provider_api,
        f"{label_prefix}/template-id": str(request.template_id),
    }
    if emit_legacy_labels:
        labels[LEGACY_REQUEST_ID_LABEL] = str(request.request_id)
    return labels


def request_id_label_selector(
    request: Request,
    *,
    label_prefix: str = _DEFAULT_LABEL_PREFIX,
) -> str:
    """Build the ``label_selector`` string for listing a request's pods."""
    return f"{label_prefix}/request-id={request.request_id}"


# ---------------------------------------------------------------------------
# Pod-spec assembly
# ---------------------------------------------------------------------------


def _resolve_container_image(template: Template) -> str:
    """Pick the container image string from a generic ``Template``.

    The kubernetes provider does not extend ``Template`` yet (the
    handler-side ``KubernetesTemplate`` lands later); for Phase B we look
    in two well-known places:

    1. ``template.image_id`` — repurposed as the container image string
       (the field is provider-agnostic at the domain layer).
    2. ``template.provider_data["kubernetes"]["container_image"]``  —
       structured field consumed by Phase B+.

    The second wins when both are set.
    """
    provider_data = getattr(template, "provider_data", None) or {}
    k8s_block = provider_data.get("kubernetes") if isinstance(provider_data, dict) else None
    if isinstance(k8s_block, dict):
        image = k8s_block.get("container_image")
        if image:
            return str(image)
    image_id = getattr(template, "image_id", None)
    if image_id:
        return str(image_id)
    raise ValueError(
        "Kubernetes template is missing a container image — set ``image_id`` or "
        "``provider_data.kubernetes.container_image``."
    )


def _resolve_resource_requests(template: Template) -> Optional[dict[str, str]]:
    """Extract optional resource requests/limits from a template."""
    provider_data = getattr(template, "provider_data", None) or {}
    if not isinstance(provider_data, dict):
        return None
    k8s_block = provider_data.get("kubernetes")
    if not isinstance(k8s_block, dict):
        return None
    requests = k8s_block.get("resource_requests")
    if isinstance(requests, dict):
        return {str(k): str(v) for k, v in requests.items()}
    return None


def _resolve_resource_limits(template: Template) -> Optional[dict[str, str]]:
    """Extract optional resource limits from a template."""
    provider_data = getattr(template, "provider_data", None) or {}
    if not isinstance(provider_data, dict):
        return None
    k8s_block = provider_data.get("kubernetes")
    if not isinstance(k8s_block, dict):
        return None
    limits = k8s_block.get("resource_limits")
    if isinstance(limits, dict):
        return {str(k): str(v) for k, v in limits.items()}
    return None


def _resolve_command(template: Template) -> Optional[list[str]]:
    """Extract optional container command from a template."""
    provider_data = getattr(template, "provider_data", None) or {}
    if not isinstance(provider_data, dict):
        return None
    k8s_block = provider_data.get("kubernetes")
    if not isinstance(k8s_block, dict):
        return None
    command = k8s_block.get("command")
    if isinstance(command, list):
        return [str(c) for c in command]
    return None


def _resolve_args(template: Template) -> Optional[list[str]]:
    """Extract optional container args from a template."""
    provider_data = getattr(template, "provider_data", None) or {}
    if not isinstance(provider_data, dict):
        return None
    k8s_block = provider_data.get("kubernetes")
    if not isinstance(k8s_block, dict):
        return None
    args = k8s_block.get("args")
    if isinstance(args, list):
        return [str(a) for a in args]
    return None


def build_pod_spec(
    template: Template,
    request: Request,
    *,
    pod_name: str,
    machine_id: str,
    namespace: str,
    provider_api: str = "KubernetesPod",
    config: Optional[KubernetesProviderConfig] = None,
) -> "V1Pod":
    """Build a single ``V1Pod`` for the supplied template and request.

    Mandatory invariants:

    * ``restartPolicy: Never`` is always set — ORB controls retry semantics
      and a self-restarting container would defeat per-pod release.
    * Labels include ``orb.io/managed=true`` and ``orb.io/request-id``;
      callers can filter by these to scope list operations.

    Args:
        template: Source ORB template.
        request: Request the pod belongs to.
        pod_name: ``metadata.name`` for the pod.
        machine_id: ORB machine identifier; included as a label.
        namespace: Target Kubernetes namespace.
        provider_api: Provider API key stamped on labels.
        config: Optional provider config — when supplied, its
            ``default_node_selector`` / ``default_tolerations`` /
            ``default_image_pull_secret`` / ``label_prefix`` /
            ``emit_legacy_labels`` fields are applied as defaults.

    Returns:
        A fully populated ``V1Pod`` ready for ``create_namespaced_pod``.
    """
    # The kubernetes SDK is imported lazily so that simply importing
    # ``pod_spec`` (e.g. from a config validator) does not require the
    # ``[kubernetes]`` extra to be installed.
    from kubernetes.client import (  # noqa: PLC0415
        V1Container,
        V1LocalObjectReference,
        V1ObjectMeta,
        V1Pod,
        V1PodSpec,
        V1ResourceRequirements,
        V1Toleration,
    )

    label_prefix = config.label_prefix if config is not None else _DEFAULT_LABEL_PREFIX
    emit_legacy_labels = config.emit_legacy_labels if config is not None else True

    labels = build_pod_labels(
        request,
        machine_id=machine_id,
        provider_api=provider_api,
        label_prefix=label_prefix,
        emit_legacy_labels=emit_legacy_labels,
    )

    image = _resolve_container_image(template)
    requests = _resolve_resource_requests(template)
    limits = _resolve_resource_limits(template)
    command = _resolve_command(template)
    args = _resolve_args(template)

    resources: Optional[V1ResourceRequirements] = None
    if requests or limits:
        resources = V1ResourceRequirements(requests=requests, limits=limits)

    container = V1Container(
        name="orb",
        image=image,
        command=command,
        args=args,
        resources=resources,
    )

    node_selector: Optional[dict[str, str]] = None
    tolerations: Optional[list[V1Toleration]] = None
    image_pull_secrets: Optional[list[V1LocalObjectReference]] = None

    if config is not None:
        if config.default_node_selector:
            node_selector = dict(config.default_node_selector)
        if config.default_tolerations:
            tolerations = [V1Toleration(**dict(t)) for t in config.default_tolerations]
        if config.default_image_pull_secret:
            image_pull_secrets = [V1LocalObjectReference(name=config.default_image_pull_secret)]

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

    pod = V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=V1ObjectMeta(
            name=pod_name,
            namespace=namespace,
            labels=labels,
        ),
        spec=V1PodSpec(**pod_spec_kwargs),
    )
    return pod


__all__ = [
    "LEGACY_REQUEST_ID_LABEL",
    "build_pod_labels",
    "build_pod_spec",
    "make_pod_name",
    "request_id_label_selector",
]
