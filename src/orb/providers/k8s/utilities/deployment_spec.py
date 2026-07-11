"""Deployment-spec construction helpers.

Builds ``kubernetes.client.V1Deployment`` objects for the
``Deployment`` provider-API key.  The pod template embedded in
the deployment is built from the same ORB ``Template`` / provider-config
plumbing as :mod:`orb.providers.k8s.utilities.pod_spec` so a
deployment pod is structurally identical to a stand-alone Pod handler
pod (image + resources + node-selector + tolerations + image-pull
secret).

The deployment selector matches the request-id label, and the pod
template inherits the full ORB label set (``managed`` / ``request-id``
/ ``machine-id`` / ``provider-api`` / ``template-id`` plus the optional
legacy label).  Pod names are assigned by the Deployment controller
(``<deployment-name>-<replicaset-hash>-<suffix>``) rather than by ORB —
the handler reads them back via a label-selector list.

Lives under ``providers/k8s/`` so the kubernetes SDK imports stay
confined to the provider tree (enforced by the
``test_k8s_leak_detection`` architecture test).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template_aggregate import upcast_to_k8s_template
from orb.providers.k8s.utilities.pod_spec import (
    _DEFAULT_LABEL_PREFIX,
    apply_pod_spec_override,
    build_container_env,
    build_container_probe,
    build_container_resources,
    build_container_volume_mounts,
    build_pod_labels,
    build_pod_security_context,
    build_pod_tolerations,
    build_pod_volumes,
    resolve_image_pull_secret_name,
    resolve_node_selector,
)

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import V1Deployment


# ---------------------------------------------------------------------------
# Deployment name helpers
# ---------------------------------------------------------------------------

# DNS-1123 label limit for the deployment name.  Pods spawned by a
# deployment inherit the name as a prefix and append a replicaset hash
# plus a pod-suffix (~16 chars), so the deployment name needs headroom.
_DEPLOYMENT_NAME_MAX_LEN = 47  # 63 - 16-char controller suffix budget


def make_deployment_name(request_id: str) -> str:
    """Build a deterministic Deployment name for an ORB request."""
    prefix = (request_id or "unknown")[:8]
    name = f"orb-{prefix}"
    if len(name) > _DEPLOYMENT_NAME_MAX_LEN:  # pragma: no cover — defensive
        name = name[:_DEPLOYMENT_NAME_MAX_LEN]
    return name


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
    provider_api: str = "Deployment",
    config: Optional[K8sProviderConfig] = None,
) -> V1Deployment:
    """Build a ``V1Deployment`` for ``request`` with the given replica count."""
    from kubernetes.client import (
        V1Container,
        V1Deployment,
        V1DeploymentSpec,
        V1LabelSelector,
        V1LocalObjectReference,
        V1ObjectMeta,
        V1Pod,
        V1PodSpec,
        V1PodTemplateSpec,
    )

    if replicas < 0:
        raise ValueError(f"replicas must be >= 0, got {replicas}")

    k8s_template = upcast_to_k8s_template(template)

    label_prefix = config.label_prefix if config is not None else _DEFAULT_LABEL_PREFIX
    emit_legacy_labels = config.emit_legacy_labels if config is not None else True

    operator_labels = k8s_template.resolve_pod_labels()

    deployment_labels = build_pod_labels(
        request,
        machine_id=deployment_name,
        provider_api=provider_api,
        label_prefix=label_prefix,
        emit_legacy_labels=emit_legacy_labels,
        extra_labels=operator_labels,
    )
    deployment_labels.pop(f"{label_prefix}/machine-id", None)

    pod_template_labels = dict(deployment_labels)

    selector_match_labels: dict[str, str] = {
        f"{label_prefix}/request-id": str(request.request_id),
        f"{label_prefix}/provider-api": provider_api,
    }

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
        "restart_policy": "Always",
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

    pod_template = V1PodTemplateSpec(
        metadata=V1ObjectMeta(
            labels=pod_template_labels,
            annotations=(dict(k8s_template.annotations) if k8s_template.annotations else None),
        ),
        spec=V1PodSpec(**pod_spec_kwargs),
    )

    if k8s_template.pod_spec_override:
        transient = V1Pod(spec=pod_template.spec)
        merged = apply_pod_spec_override(transient, k8s_template.pod_spec_override)
        pod_template.spec = merged.spec

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
