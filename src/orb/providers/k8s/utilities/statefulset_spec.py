"""StatefulSet-spec construction helpers.

Builds ``kubernetes.client.V1StatefulSet`` objects for the
``StatefulSet`` provider-API key.  The pod template embedded in
the StatefulSet is built from the same ORB ``Template`` / provider-config
plumbing as :mod:`orb.providers.k8s.utilities.deployment_spec` so
the pods are structurally identical to a Deployment pod (image +
resources + node-selector + tolerations + image-pull secret) except for
the controller-stamped names.

Unlike a Deployment, the StatefulSet controller assigns pod names
deterministically as ``<statefulset-name>-<ordinal>`` (``ordinal`` is
0-indexed).  The handler relies on this contract for the release path:
scale-down always evicts the highest-ordinal pods first.

Lives under ``providers/k8s/`` so the kubernetes SDK imports stay
confined to the provider tree (enforced by the
``test_k8s_leak_detection`` architecture test).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.utilities.pod_spec import (
    _DEFAULT_LABEL_PREFIX,
    build_pod_labels,
)

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import V1StatefulSet


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

# DNS-1123 label limit for the StatefulSet name.  Pods spawned by a
# StatefulSet append ``-<ordinal>`` so we leave headroom for up to 5
# decimal digits plus the hyphen (matching the practical limit for
# replicas).
_STATEFULSET_NAME_MAX_LEN = 57  # 63 - len("-99999")


def make_statefulset_name(request_id: str) -> str:
    """Build a deterministic StatefulSet name for an ORB request.

    Pattern: ``orb-{request_id[:8]}``.  Pod names are stamped by the
    controller as ``orb-{request_id[:8]}-<ordinal>``.

    Args:
        request_id: ORB request UUID (string).  Only the first 8 chars
            are used so the name stays compact.

    Returns:
        A DNS-1123 conformant StatefulSet name.
    """
    prefix = (request_id or "unknown")[:8]
    name = f"orb-{prefix}"
    if len(name) > _STATEFULSET_NAME_MAX_LEN:  # pragma: no cover — defensive
        name = name[:_STATEFULSET_NAME_MAX_LEN]
    return name


# ---------------------------------------------------------------------------
# Pod-template field extraction (parallel with deployment_spec)
# ---------------------------------------------------------------------------


def _resolve_container_image(template: Template) -> str:
    """Pick the container image string from a generic ``Template``."""
    provider_data = getattr(template, "provider_data", None) or {}
    k8s_block = provider_data.get("k8s") if isinstance(provider_data, dict) else None
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
    provider_data = getattr(template, "provider_data", None) or {}
    if not isinstance(provider_data, dict):
        return None
    k8s_block = provider_data.get("k8s")
    if not isinstance(k8s_block, dict):
        return None
    requests = k8s_block.get("resource_requests")
    if isinstance(requests, dict):
        return {str(k): str(v) for k, v in requests.items()}
    return None


def _resolve_resource_limits(template: Template) -> Optional[dict[str, str]]:
    provider_data = getattr(template, "provider_data", None) or {}
    if not isinstance(provider_data, dict):
        return None
    k8s_block = provider_data.get("k8s")
    if not isinstance(k8s_block, dict):
        return None
    limits = k8s_block.get("resource_limits")
    if isinstance(limits, dict):
        return {str(k): str(v) for k, v in limits.items()}
    return None


def _resolve_command(template: Template) -> Optional[list[str]]:
    provider_data = getattr(template, "provider_data", None) or {}
    if not isinstance(provider_data, dict):
        return None
    k8s_block = provider_data.get("k8s")
    if not isinstance(k8s_block, dict):
        return None
    command = k8s_block.get("command")
    if isinstance(command, list):
        return [str(c) for c in command]
    return None


def _resolve_args(template: Template) -> Optional[list[str]]:
    provider_data = getattr(template, "provider_data", None) or {}
    if not isinstance(provider_data, dict):
        return None
    k8s_block = provider_data.get("k8s")
    if not isinstance(k8s_block, dict):
        return None
    args = k8s_block.get("args")
    if isinstance(args, list):
        return [str(a) for a in args]
    return None


def _resolve_service_name(template: Template, fallback: str) -> str:
    """Resolve the ``spec.serviceName`` for a StatefulSet.

    The StatefulSet API requires a non-empty governing service name even
    when no headless Service is actually deployed.  We accept an explicit
    override via the template's ``service_name`` field and otherwise
    derive a deterministic value from the StatefulSet name.
    """
    provider_data = getattr(template, "provider_data", None) or {}
    if isinstance(provider_data, dict):
        k8s_block = provider_data.get("k8s")
        if isinstance(k8s_block, dict):
            name = k8s_block.get("service_name")
            if isinstance(name, str) and name:
                return name
    return fallback


# ---------------------------------------------------------------------------
# StatefulSet-spec assembly
# ---------------------------------------------------------------------------


def build_statefulset_spec(
    template: Template,
    request: Request,
    *,
    statefulset_name: str,
    namespace: str,
    replicas: int,
    provider_api: str = "StatefulSet",
    config: Optional[K8sProviderConfig] = None,
) -> "V1StatefulSet":
    """Build a ``V1StatefulSet`` for ``request`` with the given replica count.

    Mandatory invariants:

    * ``spec.replicas`` is exactly ``replicas``.
    * ``spec.selector.matchLabels`` includes the ORB ``request-id`` label
      so the StatefulSet uniquely owns its pods.
    * The pod template's ``restartPolicy`` is ``Always`` — StatefulSet
      replicas must be restartable by the controller for the
      ordinal-based scale-down contract to work.  Per-machine retry
      semantics are owned by ORB at the *request* level.
    * ``spec.serviceName`` is set (required by the StatefulSet API).  The
      handler does NOT create the governing headless Service inline — the
      operator is expected to provision it ahead of time when network
      identity is needed.  The serviceName field can still be a
      placeholder when no Service exists; the StatefulSet controller does
      not validate the Service's existence.

    Pod-level labels intentionally **omit** the ``machine-id`` label
    because the controller assigns pod names deterministically as
    ``<statefulset-name>-<ordinal>``; the handler reads back ordinals
    via :meth:`list_namespaced_pod`.

    Args:
        template: Source ORB template.
        request: Request the StatefulSet belongs to.
        statefulset_name: ``metadata.name`` for the StatefulSet.
        namespace: Target Kubernetes namespace.
        replicas: ``spec.replicas`` value (must be >= 0).
        provider_api: Provider API key stamped on labels.
        config: Optional provider config — when supplied, its
            ``default_node_selector`` / ``default_tolerations`` /
            ``default_image_pull_secret`` / ``label_prefix`` /
            ``emit_legacy_labels`` fields are applied as defaults.

    Returns:
        A fully populated ``V1StatefulSet`` ready for
        ``create_namespaced_stateful_set``.
    """
    # Lazy import keeps ``utilities`` clean of unconditional kubernetes
    # SDK imports for callers that only need the helpers above.
    from kubernetes.client import (  # noqa: PLC0415
        V1Container,
        V1LabelSelector,
        V1LocalObjectReference,
        V1ObjectMeta,
        V1PodSpec,
        V1PodTemplateSpec,
        V1ResourceRequirements,
        V1StatefulSet,
        V1StatefulSetSpec,
        V1Toleration,
    )

    if replicas < 0:
        raise ValueError(f"replicas must be >= 0, got {replicas}")

    label_prefix = config.label_prefix if config is not None else _DEFAULT_LABEL_PREFIX
    emit_legacy_labels = config.emit_legacy_labels if config is not None else True

    # StatefulSet-level labels (applied to the StatefulSet object itself).
    # ``machine-id`` is not meaningful on the StatefulSet (it is shared by
    # all replicas) so we drop it from the label set.
    statefulset_labels = build_pod_labels(
        request,
        machine_id=statefulset_name,
        provider_api=provider_api,
        label_prefix=label_prefix,
        emit_legacy_labels=emit_legacy_labels,
    )
    statefulset_labels.pop(f"{label_prefix}/machine-id", None)

    # Pod-template labels (applied to every replica).  Same as above —
    # the controller stamps pod names with ordinals so a fixed
    # ``machine-id`` would be wrong; the handler reads pod names back via
    # the selector.
    pod_template_labels = dict(statefulset_labels)

    # Selector matches the request-id + provider-api label so the
    # StatefulSet owns exactly the pods spawned for this request.
    selector_match_labels: dict[str, str] = {
        f"{label_prefix}/request-id": str(request.request_id),
        f"{label_prefix}/provider-api": provider_api,
    }

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
        # StatefulSet-managed pods MUST use restartPolicy Always — the
        # controller's reconciliation contract requires it.  ORB controls
        # retry semantics at the *request* level via release_hosts; the
        # controller restarts crashed containers within a pod.
        "restart_policy": "Always",
    }
    if node_selector is not None:
        pod_spec_kwargs["node_selector"] = node_selector
    if tolerations is not None:
        pod_spec_kwargs["tolerations"] = tolerations
    if image_pull_secrets is not None:
        pod_spec_kwargs["image_pull_secrets"] = image_pull_secrets

    pod_template = V1PodTemplateSpec(
        metadata=V1ObjectMeta(labels=pod_template_labels),
        spec=V1PodSpec(**pod_spec_kwargs),
    )

    service_name = _resolve_service_name(template, fallback=statefulset_name)

    statefulset_spec = V1StatefulSetSpec(
        replicas=replicas,
        selector=V1LabelSelector(match_labels=selector_match_labels),
        service_name=service_name,
        template=pod_template,
    )

    return V1StatefulSet(
        api_version="apps/v1",
        kind="StatefulSet",
        metadata=V1ObjectMeta(
            name=statefulset_name,
            namespace=namespace,
            labels=statefulset_labels,
        ),
        spec=statefulset_spec,
    )


# ---------------------------------------------------------------------------
# Ordinal helpers
# ---------------------------------------------------------------------------


def parse_statefulset_pod_ordinal(pod_name: str, statefulset_name: str) -> Optional[int]:
    """Extract the ordinal suffix from a StatefulSet pod name.

    StatefulSet pods are named ``<statefulset-name>-<ordinal>``.  Returns
    the integer ordinal or ``None`` when ``pod_name`` does not match the
    expected pattern (e.g. a pod that does not belong to ``statefulset_name``
    or a malformed name).
    """
    if not pod_name or not statefulset_name:
        return None
    prefix = f"{statefulset_name}-"
    if not pod_name.startswith(prefix):
        return None
    suffix = pod_name[len(prefix) :]
    if not suffix.isdigit():
        return None
    return int(suffix)


__all__ = [
    "build_statefulset_spec",
    "make_statefulset_name",
    "parse_statefulset_pod_ordinal",
]
