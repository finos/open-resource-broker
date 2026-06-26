"""Deployment-spec construction helpers.

Builds ``kubernetes.client.V1Deployment`` objects for the
``KubernetesDeployment`` provider-API key.  The pod template embedded in
the deployment is built from the same ORB ``Template`` / provider-config
plumbing as :mod:`orb.providers.kubernetes.utilities.pod_spec` so a
deployment pod is structurally identical to a stand-alone Pod handler
pod (image + resources + node-selector + tolerations + image-pull
secret).

The deployment selector matches the request-id label, and the pod
template inherits the full ORB label set (``managed`` / ``request-id``
/ ``machine-id`` / ``provider-api`` / ``template-id`` plus the optional
legacy label).  Pod names are assigned by the Deployment controller
(``<deployment-name>-<replicaset-hash>-<suffix>``) rather than by ORB —
the handler reads them back via a label-selector list.

Lives under ``providers/kubernetes/`` so the kubernetes SDK imports stay
confined to the provider tree (enforced by the
``test_kubernetes_leak_detection`` architecture test).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.utilities.pod_spec import (
    _DEFAULT_LABEL_PREFIX,
    build_pod_labels,
)

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import V1Deployment


# ---------------------------------------------------------------------------
# Deployment name / selector helpers
# ---------------------------------------------------------------------------

# DNS-1123 label limit for the deployment name.  Pods spawned by a
# deployment inherit the name as a prefix and append a replicaset hash
# plus a pod-suffix (~16 chars), so the deployment name needs headroom.
_DEPLOYMENT_NAME_MAX_LEN = 47  # 63 - 16-char controller suffix budget


def make_deployment_name(request_id: str) -> str:
    """Build a deterministic Deployment name for an ORB request.

    Pattern: ``orb-{request_id[:8]}``.  The trailing sequence used by the
    Pod handler is omitted — one Deployment per request supports N replicas.

    Args:
        request_id: ORB request UUID (string).  Only the first 8 chars
            are used so the name stays compact and human-readable.

    Returns:
        A DNS-1123 conformant Deployment name.
    """
    prefix = (request_id or "unknown")[:8]
    name = f"orb-{prefix}"
    if len(name) > _DEPLOYMENT_NAME_MAX_LEN:  # pragma: no cover — defensive
        name = name[:_DEPLOYMENT_NAME_MAX_LEN]
    return name


# ---------------------------------------------------------------------------
# Pod-template field extraction (shared with the Pod handler at the field
# level but assembled here so we can apply the additional Deployment
# pod-template invariants — labels minus ``machine-id``, restart policy
# ``Always``, etc.).
# ---------------------------------------------------------------------------


def _resolve_container_image(template: Template) -> str:
    """Pick the container image string from a generic ``Template``."""
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


# ---------------------------------------------------------------------------
# Deployment-spec assembly
# ---------------------------------------------------------------------------


def build_deployment_spec(
    template: Template,
    request: Request,
    *,
    deployment_name: str,
    namespace: str,
    replicas: int,
    provider_api: str = "KubernetesDeployment",
    config: Optional[KubernetesProviderConfig] = None,
) -> "V1Deployment":
    """Build a ``V1Deployment`` for ``request`` with the given replica count.

    Mandatory invariants:

    * ``spec.replicas`` is exactly ``replicas`` (caller decides the value;
      typically ``request.requested_count``).
    * ``spec.selector.matchLabels`` includes the ORB ``request-id`` label
      so the deployment uniquely owns the pods it spawns and the handler
      can list them with the same selector.
    * The pod template's ``restartPolicy`` is ``Always`` — Deployment
      replicas must be restartable by the controller for the scale-down
      contract (and for the pod-deletion-cost annotation strategy) to
      work; per-machine retry semantics are owned by ORB at the request
      level, not the pod level.

    Pod-level labels intentionally **omit** the ``machine-id`` label
    because the controller assigns pod names, and a single label value
    cannot fan out across N pods.  The handler discovers per-pod
    ``machine_id`` (i.e. the controller-assigned pod name) on read-back
    via :meth:`list_namespaced_pod`.

    Args:
        template: Source ORB template.
        request: Request the deployment belongs to.
        deployment_name: ``metadata.name`` for the deployment.
        namespace: Target Kubernetes namespace.
        replicas: ``spec.replicas`` value (must be >= 0).
        provider_api: Provider API key stamped on labels.
        config: Optional provider config — when supplied, its
            ``default_node_selector`` / ``default_tolerations`` /
            ``default_image_pull_secret`` / ``label_prefix`` /
            ``emit_legacy_labels`` fields are applied as defaults.

    Returns:
        A fully populated ``V1Deployment`` ready for
        ``create_namespaced_deployment``.
    """
    # Lazy import keeps ``utilities`` clean of unconditional kubernetes
    # SDK imports for callers that only need the helpers above.
    from kubernetes.client import (  # noqa: PLC0415
        V1Container,
        V1Deployment,
        V1DeploymentSpec,
        V1LabelSelector,
        V1LocalObjectReference,
        V1ObjectMeta,
        V1PodSpec,
        V1PodTemplateSpec,
        V1ResourceRequirements,
        V1Toleration,
    )

    if replicas < 0:
        raise ValueError(f"replicas must be >= 0, got {replicas}")

    label_prefix = config.label_prefix if config is not None else _DEFAULT_LABEL_PREFIX
    emit_legacy_labels = config.emit_legacy_labels if config is not None else True

    # Deployment-level labels (applied to the Deployment object itself).
    # ``machine-id`` is not a label on the Deployment because the
    # Deployment is shared by all replicas.  We reuse the helper but
    # then strip ``machine-id`` so it does not point to a non-existent
    # pod.
    deployment_labels = build_pod_labels(
        request,
        machine_id=deployment_name,
        provider_api=provider_api,
        label_prefix=label_prefix,
        emit_legacy_labels=emit_legacy_labels,
    )
    # The Deployment itself is not a pod and has no machine_id — drop the
    # label so it does not get conflated with a real machine.
    deployment_labels.pop(f"{label_prefix}/machine-id", None)

    # Pod-template labels (applied to every replica).  Same as above —
    # the controller stamps pod names, so a fixed ``machine-id`` label
    # would be wrong; the handler reads pod names back via the selector.
    pod_template_labels = dict(deployment_labels)

    # Selector matches the request-id label so the Deployment owns
    # exactly the pods spawned for this request.
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
        # Deployment-managed pods MUST use restartPolicy Always — the
        # controller's reconciliation contract requires it.  ORB
        # controls retry semantics at the *request* level via
        # release_hosts; the controller restarts crashed containers
        # within a pod.
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

    deployment_spec = V1DeploymentSpec(
        replicas=replicas,
        selector=V1LabelSelector(match_labels=selector_match_labels),
        template=pod_template,
    )

    return V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=V1ObjectMeta(
            name=deployment_name,
            namespace=namespace,
            labels=deployment_labels,
        ),
        spec=deployment_spec,
    )


__all__ = [
    "build_deployment_spec",
    "make_deployment_name",
]
