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
from orb.providers.k8s.configuration.config import K8sNamingConfig, K8sProviderConfig
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
    resolve_restart_policy,
)

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import V1Job


_JOB_NAME_MAX_LEN = 50  # 63 - "-XXXXX" plus a margin for the controller suffix

_DEFAULT_JOB_UUID_CHARS = 8


def make_job_name(
    request_id: str,
    naming: Optional[K8sNamingConfig] = None,
) -> str:
    """Build a deterministic Job name for an ORB request.

    When *naming* is ``None`` the historical ``orb-{uuid[:8]}`` pattern is
    reproduced for backward compatibility.
    """
    if naming is not None:
        pfx = naming.prefix
        n_chars = naming.uuid_chars
        max_len = naming.max_job_name_len
    else:
        pfx = "orb"
        n_chars = _DEFAULT_JOB_UUID_CHARS
        max_len = _JOB_NAME_MAX_LEN
    rid = request_id or "unknown"
    # Strip a leading req- / req_ prefix so the uuid segment is pure hex.
    if rid.startswith(("req-", "req_")):
        rid = rid[4:]
    safe = rid.replace("-", "")
    uuid_seg = safe[:n_chars] if safe else "unknown"
    name = f"{pfx}-{uuid_seg}"
    if len(name) > max_len:  # pragma: no cover — defensive
        name = name[:max_len]
    return name


def build_job_spec(
    template: Template,
    request: Request,
    *,
    job_name: str,
    namespace: str,
    parallelism: int,
    provider_api: str = "Job",
    config: Optional[K8sProviderConfig] = None,
) -> V1Job:
    """Build a ``V1Job`` for ``request`` with the given parallelism.

    The Job handler overrides ``parallelism`` / ``completions`` from the
    typed :class:`K8sTemplate` fields when set; otherwise both default
    to the supplied ``parallelism`` (derived from ``request.requested_count``).
    """
    from kubernetes.client import (
        V1Container,
        V1Job,
        V1JobSpec,
        V1LabelSelector,
        V1LocalObjectReference,
        V1ObjectMeta,
        V1Pod,
        V1PodSpec,
        V1PodTemplateSpec,
    )

    if parallelism < 1:
        raise ValueError(f"parallelism must be >= 1, got {parallelism}")

    k8s_template = upcast_to_k8s_template(template)

    label_prefix = config.label_prefix if config is not None else _DEFAULT_LABEL_PREFIX
    emit_legacy_labels = config.emit_legacy_labels if config is not None else True

    operator_labels = k8s_template.resolve_pod_labels()

    job_labels = build_pod_labels(
        request,
        machine_id=job_name,
        provider_api=provider_api,
        label_prefix=label_prefix,
        emit_legacy_labels=emit_legacy_labels,
        extra_labels=operator_labels,
    )
    job_labels.pop(f"{label_prefix}/machine-id", None)

    pod_template_labels = dict(job_labels)

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

    restart_policy = resolve_restart_policy(
        k8s_template,
        config=config,
        kind_default="Never",
        allowed_values=frozenset({"Never", "OnFailure"}),
    )
    pod_spec_kwargs: dict[str, Any] = {
        "containers": [container],
        "restart_policy": restart_policy,
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
        merged = apply_pod_spec_override(
            transient, k8s_template.pod_spec_override, expected_restart_policy=restart_policy
        )
        pod_template.spec = merged.spec

    effective_parallelism = (
        int(k8s_template.parallelism) if k8s_template.parallelism is not None else parallelism
    )
    effective_completions = (
        int(k8s_template.completions) if k8s_template.completions is not None else parallelism
    )

    job_spec_kwargs: dict[str, Any] = {
        "parallelism": effective_parallelism,
        "completions": effective_completions,
        "backoff_limit": 0,
        "manual_selector": True,
        "selector": V1LabelSelector(match_labels=selector_match_labels),
        "template": pod_template,
    }
    if k8s_template.ttl_seconds_after_finished is not None:
        job_spec_kwargs["ttl_seconds_after_finished"] = k8s_template.ttl_seconds_after_finished
    if k8s_template.active_deadline_seconds is not None:
        job_spec_kwargs["active_deadline_seconds"] = k8s_template.active_deadline_seconds

    job_spec = V1JobSpec(**job_spec_kwargs)

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
    "_JOB_NAME_MAX_LEN",
]
