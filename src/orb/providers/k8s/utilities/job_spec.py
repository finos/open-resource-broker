"""Job-spec construction helpers.

Builds ``kubernetes.client.V1Job`` objects for the ``Job``
provider-API key.  The pod template embedded in the Job is built from
the same ORB ``Template`` / provider-config plumbing as
:mod:`orb.providers.k8s.utilities.pod_spec` so a Job pod is
structurally identical to a stand-alone Pod (image + resources +
node-selector + tolerations + image-pull secret) apart from the
controller-stamped names and the run-to-completion semantics.

Job invariants the handler relies on:

* ``spec.parallelism = spec.completions = N`` — N pods are launched
  concurrently and each must complete successfully for the Job to be
  considered ``Complete``.  ``parallelism`` cannot be safely mutated
  post-creation, so selective release is not supported (the handler
  always deletes the whole Job).
* ``spec.backoffLimit = 0`` — ORB owns retry semantics at the *request*
  level.  The Job controller must NOT silently restart failed pods.
* ``spec.template.spec.restartPolicy = Never`` — ``backoffLimit=0``
  requires a non-``Always`` restart policy at the pod level.  ``Never``
  is consistent with the stand-alone Pod handler's invariants and lets
  ORB observe terminal pod failures rather than have the kubelet retry
  the container in place.

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
    from kubernetes.client import V1Job


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

# DNS-1123 label limit for the Job name.  Pods spawned by a Job append a
# random suffix (typically 5 chars) so we leave headroom.
_JOB_NAME_MAX_LEN = 50  # 63 - "-XXXXX" plus a margin for the controller suffix


def make_job_name(request_id: str) -> str:
    """Build a deterministic Job name for an ORB request.

    Pattern: ``orb-{request_id[:8]}``.  The Job controller assigns pod
    names of the form ``<job-name>-<random>``.

    Args:
        request_id: ORB request UUID (string).  Only the first 8 chars
            are used so the name stays compact.

    Returns:
        A DNS-1123 conformant Job name.
    """
    prefix = (request_id or "unknown")[:8]
    name = f"orb-{prefix}"
    if len(name) > _JOB_NAME_MAX_LEN:  # pragma: no cover — defensive
        name = name[:_JOB_NAME_MAX_LEN]
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


# ---------------------------------------------------------------------------
# Job-spec assembly
# ---------------------------------------------------------------------------


def build_job_spec(
    template: Template,
    request: Request,
    *,
    job_name: str,
    namespace: str,
    parallelism: int,
    provider_api: str = "Job",
    config: Optional[K8sProviderConfig] = None,
) -> "V1Job":
    """Build a ``V1Job`` for ``request`` with the given parallelism.

    Mandatory invariants:

    * ``spec.parallelism = spec.completions = parallelism`` — N pods
      launched concurrently, each must complete successfully.
    * ``spec.backoffLimit = 0`` — ORB owns retry; the Job controller
      must not silently restart failed pods.
    * ``spec.template.spec.restartPolicy = Never`` — ``backoffLimit=0``
      requires a non-``Always`` restart policy at the pod level.
    * ``spec.selector.matchLabels`` includes the ORB ``request-id`` label
      so the Job uniquely owns the pods it spawns and the handler can
      list them with the same selector.  We also set
      ``spec.manualSelector = True`` so the API server accepts our
      explicit selector instead of auto-generating one.

    Pod-level labels intentionally **omit** the ``machine-id`` label
    because the controller assigns pod names; the handler discovers
    per-pod ``machine_id`` on read-back via :meth:`list_namespaced_pod`.

    Args:
        template: Source ORB template.
        request: Request the Job belongs to.
        job_name: ``metadata.name`` for the Job.
        namespace: Target Kubernetes namespace.
        parallelism: Number of pods (parallelism AND completions); must
            be >= 1.
        provider_api: Provider API key stamped on labels.
        config: Optional provider config — when supplied, its
            ``default_node_selector`` / ``default_tolerations`` /
            ``default_image_pull_secret`` / ``label_prefix`` /
            ``emit_legacy_labels`` fields are applied as defaults.

    Returns:
        A fully populated ``V1Job`` ready for
        ``create_namespaced_job``.
    """
    # Lazy import keeps ``utilities`` clean of unconditional kubernetes
    # SDK imports for callers that only need the helpers above.
    from kubernetes.client import (  # noqa: PLC0415
        V1Container,
        V1Job,
        V1JobSpec,
        V1LabelSelector,
        V1LocalObjectReference,
        V1ObjectMeta,
        V1PodSpec,
        V1PodTemplateSpec,
        V1ResourceRequirements,
        V1Toleration,
    )

    if parallelism < 1:
        raise ValueError(f"parallelism must be >= 1, got {parallelism}")

    label_prefix = config.label_prefix if config is not None else _DEFAULT_LABEL_PREFIX
    emit_legacy_labels = config.emit_legacy_labels if config is not None else True

    # Job-level labels (applied to the Job object itself).  ``machine-id``
    # is not meaningful on the Job (it is shared by all replicas) so we
    # drop it from the label set.
    job_labels = build_pod_labels(
        request,
        machine_id=job_name,
        provider_api=provider_api,
        label_prefix=label_prefix,
        emit_legacy_labels=emit_legacy_labels,
    )
    job_labels.pop(f"{label_prefix}/machine-id", None)

    # Pod-template labels (applied to every pod the Job creates).
    # ``machine-id`` omitted — the controller stamps pod names.
    pod_template_labels = dict(job_labels)

    # Selector matches the request-id + provider-api label so the Job
    # uniquely owns exactly the pods spawned for this request.
    # ``manual_selector=True`` opts out of the controller auto-generated
    # selector (which would inject ``controller-uid`` and ``job-name``
    # automatically) so we get a stable, ORB-managed selector.
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
        # Job-managed pods MUST use restartPolicy ``Never`` — combined
        # with ``backoffLimit=0`` this surfaces every container failure
        # to ORB instead of being silently retried by the kubelet.
        "restart_policy": "Never",
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

    job_spec = V1JobSpec(
        parallelism=parallelism,
        completions=parallelism,
        backoff_limit=0,
        manual_selector=True,
        selector=V1LabelSelector(match_labels=selector_match_labels),
        template=pod_template,
    )

    return V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=V1ObjectMeta(
            name=job_name,
            namespace=namespace,
            labels=job_labels,
        ),
        spec=job_spec,
    )


__all__ = [
    "build_job_spec",
    "make_job_name",
]
