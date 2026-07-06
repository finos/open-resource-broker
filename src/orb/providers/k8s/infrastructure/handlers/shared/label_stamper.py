"""Label-stamping helpers for Kubernetes provider handlers.

Provides the per-request identity stamping logic shared by every controller-
based handler (Deployment, StatefulSet, Job).  Extracting it here keeps the
base class thin and lets future handlers call these functions directly without
subclassing.

ORB owns three labels on every managed workload:

* ``<prefix>/managed=true``     — marks the resource as ORB-managed.
* ``<prefix>/request-id=<id>``  — binds the workload to its request.
* ``<prefix>/template-id=<id>`` — binds the workload to its template.

For controller-based workloads (Deployment, StatefulSet, Job) the same labels
must appear on *both* the workload ``metadata.labels`` and the
``spec.template.metadata.labels`` so the label-selector reads made by the
status resolver can find the pods.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.request.aggregate import Request


def stamp_native_workload_body(
    native_body: dict[str, Any],
    *,
    workload_name: str,
    namespace: str,
    replicas: int,
    request: "Request",
    label_prefix: str,
) -> dict[str, Any]:
    """Stamp per-request identity onto a rendered native workload body.

    Used by the Deployment / StatefulSet / Job handlers when the native-spec
    escape hatch is active.  Overwrites the fields ORB owns at acquire time
    (name / namespace / replicas, request-id and managed labels) while
    preserving operator-controlled fields (pod-template selector match labels,
    container spec, ...) as-is.

    Args:
        native_body: The operator-rendered workload body dict.  Mutated in a
            deep copy — the original is not modified.
        workload_name: The name ORB assigns to the controller resource.
        namespace: The resolved namespace for this request.
        replicas: Number of desired pods.
        request: The provisioning request aggregate.
        label_prefix: The ``K8sProviderConfig.label_prefix`` value (e.g.
            ``"orb.io"``).

    Returns:
        A deep copy of ``native_body`` with ORB-owned fields stamped in.
    """
    body = copy.deepcopy(native_body)

    metadata = body.setdefault("metadata", {})
    metadata["name"] = workload_name
    metadata["namespace"] = namespace
    labels = dict(metadata.get("labels", {}) or {})
    labels[f"{label_prefix}/managed"] = "true"
    labels[f"{label_prefix}/request-id"] = str(request.request_id)
    labels[f"{label_prefix}/template-id"] = str(request.template_id)
    metadata["labels"] = labels

    spec = body.setdefault("spec", {})
    # Stamp the replica count under whichever key the workload kind uses.
    # Job uses ``parallelism``/``completions``; Deployment/StatefulSet use
    # ``replicas``.  Only overwrite keys already present in the operator body
    # so an unrecognised kind gets the ``replicas`` default.
    if "parallelism" in spec or "completions" in spec:
        spec["parallelism"] = replicas
        spec["completions"] = replicas
    else:
        spec["replicas"] = replicas

    # Ensure request-id label is in the pod-template labels too so the
    # controller's selector can match the pods.
    template_section = spec.setdefault("template", {})
    template_metadata = template_section.setdefault("metadata", {})
    template_labels = dict(template_metadata.get("labels", {}) or {})
    template_labels[f"{label_prefix}/request-id"] = str(request.request_id)
    template_labels[f"{label_prefix}/managed"] = "true"
    template_labels[f"{label_prefix}/template-id"] = str(request.template_id)
    template_metadata["labels"] = template_labels

    return body


__all__ = ["stamp_native_workload_body"]
