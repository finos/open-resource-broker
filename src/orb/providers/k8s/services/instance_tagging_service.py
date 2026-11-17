"""Kubernetes TAG_INSTANCES: write ORB machine tags onto pods as labels.

ORB sends ``TAG_INSTANCES`` with ``parameters["instance_tags"]`` mapping each
machine id to the tags to apply.  For AWS the tags become EC2 tags; here they
become labels on the pod, which is the machine for every Kubernetes handler.
The main consumer is Slurm: ``orb:node-name`` lands as
``<label_prefix>/node-name`` and the compute container reads it through a
downward API volume to start ``slurmd -N <name>``.

How tags become labels (``orb:`` keys, other keys, what is skipped) is defined
in :mod:`orb.providers.k8s.utilities.instance_tag_labels`.

Machine ids and namespaces
==========================

A Kubernetes machine id is the pod name for every handler (Pod, Deployment,
StatefulSet and Job pods alike), so the service patches ``v1/Pod`` objects
and does not care which controller owns them.  The operation does not carry
the pod's namespace, so the service looks in the namespaces the provider
manages:

* ``namespaces`` unset: the provider's ``namespace``.
* explicit ``namespaces`` list: each entry in order.
* ``namespaces: ["*"]``: the provider's ``namespace``, then a cluster-wide
  lookup of an ORB-managed pod with that name.

Labels on a pod that a controller later recreates are not carried over to the
new pod.  ORB pushes the node name again only when it names a new machine, so
a StatefulSet pod recreated under the same name loses the label.

Result
======

Like AWS, the call succeeds when every pod was patched and fails with
``TAG_OPERATION_FAILED`` otherwise.  Either way ``data["results"]`` reports each
machine (``tagged``, ``not_found``, ``failed`` or ``skipped``), the labels
written and the tags skipped with the reason.  A tag that cannot be written as
a label is skipped and reported; it does not fail the call.

RBAC
====

``patch`` on ``pods`` (already granted by ``docs/root/providers/k8s/rbac.yaml``
for the Pod handler) and, for ``namespaces: ["*"]``, cluster-scoped ``list``
on ``pods`` (already granted by the ClusterRole there).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.base.strategy import ProviderOperation, ProviderResult
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.utilities.dns_names import validate_dns_1123_subdomain
from orb.providers.k8s.utilities.instance_tag_labels import tags_to_labels

if TYPE_CHECKING:  # pragma: no cover
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

# Upper bound on concurrent pod patches, matching the Pod handler's create and
# delete fan-out.
_MAX_CONCURRENT_PATCHES = 50

_WILDCARD = "*"


class K8sInstanceTaggingService:
    """Apply per-machine tags to pods as labels (``TAG_INSTANCES``).

    Args:
        config: Provider config; supplies ``label_prefix`` and the namespaces.
        logger: Injected logging port.
        pod_handler_provider: Returns the Pod handler, whose retry, circuit
            breaker and metrics wrap the apiserver calls.
    """

    def __init__(
        self,
        *,
        config: K8sProviderConfig,
        logger: LoggingPort,
        pod_handler_provider: Callable[[], K8sPodHandler],
        max_concurrent_patches: int = _MAX_CONCURRENT_PATCHES,
    ) -> None:
        self._config = config
        self._logger = logger
        self._pod_handler_provider = pod_handler_provider
        self._max_concurrent_patches = max_concurrent_patches

    async def tag_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Write ``parameters["instance_tags"]`` onto the named pods as labels."""
        instance_tags: dict[str, dict[str, Any]] = operation.parameters.get("instance_tags") or {}
        if not instance_tags:
            return ProviderResult.success_result({"tagged_count": 0, "results": {}})

        handler = self._pod_handler_provider()
        sem = asyncio.Semaphore(self._max_concurrent_patches)
        machine_ids = [str(mid) for mid in instance_tags]
        outcomes = await asyncio.gather(
            *(self._tag_one(handler, sem, mid, instance_tags[mid] or {}) for mid in instance_tags)
        )
        results = dict(zip(machine_ids, outcomes, strict=True))

        tagged = [mid for mid, r in results.items() if r["status"] == "tagged"]
        problems = {mid: r for mid, r in results.items() if r["status"] in ("not_found", "failed")}
        data = {"tagged_count": len(tagged), "results": results}
        if not problems:
            return ProviderResult.success_result(data, {"operation": "tag_instances"})
        summary = ", ".join(
            f"{mid} ({r['status']}{': ' + r['error'] if r.get('error') else ''})"
            for mid, r in problems.items()
        )
        return ProviderResult(
            success=False,
            data=data,
            error_message=(
                f"TAG_INSTANCES failed for {len(problems)} of {len(results)} pods: {summary}"
            ),
            error_code="TAG_OPERATION_FAILED",
            metadata={"operation": "tag_instances"},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _candidate_namespaces(self) -> tuple[list[str], bool]:
        """Namespaces to look for a pod in, and whether a cluster-wide lookup follows."""
        configured = self._config.namespaces
        default = self._config.namespace or "default"
        if not configured:
            return [default], False
        wildcard = _WILDCARD in configured
        names = [default] if wildcard else []
        for ns in configured:
            if ns != _WILDCARD and ns not in names:
                names.append(ns)
        return names, wildcard

    async def _tag_one(
        self,
        handler: K8sPodHandler,
        sem: asyncio.Semaphore,
        machine_id: str,
        tags: dict[str, Any],
    ) -> dict[str, Any]:
        labels, skipped = tags_to_labels(tags, self._config.label_prefix)
        result: dict[str, Any] = {"labels": labels, "skipped_tags": skipped}
        for key, reason in skipped.items():
            self._logger.warning(
                "Kubernetes TAG_INSTANCES: not labelling pod %s with tag %s: %s",
                machine_id,
                key,
                reason,
            )
        if not labels:
            result["status"] = "skipped"
            return result
        try:
            validate_dns_1123_subdomain(machine_id, field_name="pod name")
        except ValueError as exc:
            result.update(status="failed", error=str(exc))
            return result

        async with sem:
            try:
                namespace = await self._patch_in_managed_namespace(handler, machine_id, labels)
            except Exception as exc:
                self._logger.error(
                    "Kubernetes TAG_INSTANCES: failed to label pod %s: %s",
                    machine_id,
                    exc,
                    exc_info=True,
                )
                result.update(status="failed", error=str(exc))
                return result

        if namespace is None:
            self._logger.warning(
                "Kubernetes TAG_INSTANCES: pod %s not found in the provider's namespaces",
                machine_id,
            )
            result["status"] = "not_found"
            return result
        self._logger.info(
            "Kubernetes TAG_INSTANCES: labelled pod %s/%s with %s",
            namespace,
            machine_id,
            sorted(labels),
        )
        result.update(status="tagged", namespace=namespace)
        return result

    async def _patch_in_managed_namespace(
        self, handler: K8sPodHandler, pod_name: str, labels: dict[str, str]
    ) -> Optional[str]:
        """Patch the pod in the first managed namespace that has it; return that namespace."""
        namespaces, wildcard = self._candidate_namespaces()
        for namespace in namespaces:
            if await handler.patch_pod_labels(
                namespace=namespace, pod_name=pod_name, labels=labels
            ):
                return namespace
        if not wildcard:
            return None
        namespace = await handler.find_managed_pod_namespace(pod_name)
        if namespace is None or namespace in namespaces:
            return None
        if await handler.patch_pod_labels(namespace=namespace, pod_name=pod_name, labels=labels):
            return namespace
        return None


__all__ = ["K8sInstanceTaggingService"]
