"""KubeSwiftSandboxHandler: one ``SwiftSandbox`` microVM per ORB machine.

``provider_api="KubeSwiftSandbox"`` on the Kubernetes provider.  Each machine
is a KubeSwift ``SwiftSandbox`` (``sandbox.kubeswift.io/v1alpha1``): an OCI
image booted as a microVM, either cold or checked out of a pre-booted
``SwiftSandboxPool`` (``metadata.kubeswift.poolRef``).

* acquire creates one ``SwiftSandbox`` per machine, labelled like ORB's pods
  (``managed``, ``request-id``, ``machine-id``, ``orb-instance``);
* status lists them by request id and maps phase and the ``GuestRunning``
  condition to ORB machine status;
* release deletes the named sandboxes (404 counts as deleted).

The kind is registered with ``custom_resources=`` so ORB's orphan garbage
collector covers sandboxes whose request ORB has lost.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.handler_plugins import K8sCustomResource, register_k8s_handler
from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.infrastructure.handlers.kubeswift import (
    GROUP,
    POOL_PLURAL,
    SANDBOX_KIND,
    SANDBOX_PLURAL,
    VERSION,
)
from orb.providers.k8s.infrastructure.handlers.kubeswift.sandbox_spec import (
    KubeSwiftTemplateError,
    build_spec,
    kubeswift_config,
)
from orb.providers.k8s.infrastructure.handlers.kubeswift.sandbox_status import (
    fulfilment_for,
    reportable_ip,
    sandbox_machine_status,
)
from orb.providers.k8s.utilities.labels import instance_label_key
from orb.providers.k8s.utilities.pod_spec import make_pod_name

PROVIDER_API = "KubeSwiftSandbox"
SANDBOX_RESOURCE = K8sCustomResource(GROUP, VERSION, SANDBOX_PLURAL)

# Same cap as the Pod handler's concurrent creates.
_MAX_CONCURRENT_CALLS = 50


class KubeSwiftSandboxHandler(K8sHandlerBase):
    """Provider-API ``KubeSwiftSandbox``: KubeSwift ``SwiftSandbox`` microVMs."""

    PROVIDER_API = PROVIDER_API

    # ------------------------------------------------------------------
    # acquire
    # ------------------------------------------------------------------

    def _labels(self, request: Request, machine_id: str) -> dict[str, str]:
        prefix = self._config.label_prefix
        labels = {
            f"{prefix}/managed": "true",
            f"{prefix}/request-id": str(request.request_id),
            f"{prefix}/machine-id": machine_id,
            f"{prefix}/provider-api": PROVIDER_API,
        }
        if self._config.orb_instance_id:
            labels[instance_label_key(prefix)] = self._config.orb_instance_id
        return labels

    def _get_pool(self, namespace: str, name: str) -> dict[str, Any]:
        try:
            return self.with_retry(
                self.client.custom_objects_api.get_namespaced_custom_object,
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural=POOL_PLURAL,
                name=name,
                operation_name="get_swiftsandboxpool",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                raise KubeSwiftTemplateError(
                    f"SwiftSandboxPool {namespace}/{name} not found"
                ) from exc
            raise

    def _create(
        self, namespace: str, name: str, labels: dict[str, str], spec: dict[str, Any]
    ) -> None:
        self.with_retry(
            self.client.custom_objects_api.create_namespaced_custom_object,
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=SANDBOX_PLURAL,
            body={
                "apiVersion": f"{GROUP}/{VERSION}",
                "kind": SANDBOX_KIND,
                "metadata": {"name": name, "namespace": namespace, "labels": labels},
                "spec": spec,
            },
            operation_name="create_swiftsandbox",
        )

    async def acquire_hosts(self, request: Request, template: Template) -> dict[str, Any]:
        namespace = self.resolve_namespace(template)
        count = max(int(request.requested_count), 1)
        pool_name: Optional[str] = kubeswift_config(template).get("poolRef")
        pool = await asyncio.to_thread(self._get_pool, namespace, pool_name) if pool_name else None
        spec = build_spec(template, pool)
        self._record_acquire(namespace=namespace, spec_kind=PROVIDER_API)

        names = [
            make_pod_name(str(request.request_id), seq, naming=self._config.naming)
            for seq in range(count)
        ]
        sem = asyncio.Semaphore(_MAX_CONCURRENT_CALLS)

        async def create(name: str) -> None:
            async with sem:
                labels = self._labels(request, name)
                await asyncio.to_thread(self._create, namespace, name, labels, spec)

        results = await asyncio.gather(*(create(n) for n in names), return_exceptions=True)
        created = [
            n for n, r in zip(names, results, strict=True) if not isinstance(r, BaseException)
        ]
        failed = [
            (n, str(r)) for n, r in zip(names, results, strict=True) if isinstance(r, BaseException)
        ]
        for name, error in failed:
            self._logger.warning("SwiftSandbox create failed: %s/%s: %s", namespace, name, error)
        if not created:
            raise RuntimeError(f"no SwiftSandbox created: {failed[0][1] if failed else 'unknown'}")
        return {
            "success": True,
            "resource_ids": created,
            "machine_ids": created,
            "provider_data": {
                "request_id": str(request.request_id),
                "namespace": namespace,
                "sandbox_names": created,
                "failed_sandbox_names": [n for n, _ in failed],
                "pool": pool_name,
            },
        }

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def instance_dict(self, obj: dict[str, Any]) -> dict[str, Any]:
        """Return ORB's per-instance dict for one ``SwiftSandbox``."""
        metadata = obj.get("metadata") or {}
        status = obj.get("status") or {}
        network = status.get("network") or {}
        machine_status, reason = sandbox_machine_status(obj)
        name = metadata.get("name")
        return {
            "instance_id": name,
            "resource_id": name,
            "name": name,
            "status": machine_status,
            "status_reason": reason,
            "private_ip": reportable_ip(obj),
            "public_ip": None,
            "private_dns_name": None,
            "public_dns_name": None,
            "launch_time": status.get("startedAt"),
            "instance_type": f"kubeswift/{PROVIDER_API}",
            "image_id": (obj.get("spec") or {}).get("image"),
            "subnet_id": None,
            "security_group_ids": [],
            "vpc_id": None,
            "tags": metadata.get("labels") or {},
            "provider_data": {
                "namespace": metadata.get("namespace"),
                "node_name": status.get("nodeName"),
                "pod_ip": network.get("podIP"),
                "primary_ip_scope": network.get("primaryIPScope"),
                "phase": status.get("phase"),
                "exit_code": status.get("exitCode"),
            },
        }

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        namespace = self._resolve_request_namespace(request)
        response = self.with_retry(
            self.client.custom_objects_api.list_namespaced_custom_object,
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=SANDBOX_PLURAL,
            label_selector=self.build_label_selector(request),
            operation_name="list_swiftsandboxes",
        )
        items = response.get("items") if isinstance(response, dict) else None
        instances = [self.instance_dict(obj) for obj in items or []]
        return CheckHostsStatusResult(
            instances=instances,
            fulfilment=fulfilment_for(instances, max(int(request.requested_count), 1)),
        )

    # ------------------------------------------------------------------
    # release
    # ------------------------------------------------------------------

    def _delete(self, namespace: str, name: str) -> None:
        try:
            self.with_retry(
                self.client.custom_objects_api.delete_namespaced_custom_object,
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural=SANDBOX_PLURAL,
                name=name,
                operation_name="delete_swiftsandbox",
            )
        except Exception as exc:
            if not self.is_not_found(exc):
                raise

    async def release_hosts(
        self, machine_ids: list[str], provider_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Delete the named sandboxes; same partial-failure contract as the Pod handler.

        Returns ``deleted`` and ``failed_deletes`` (``(name, reason)`` tuples) and
        raises only when every delete failed.
        """
        if not machine_ids:
            return {"deleted": [], "failed_deletes": []}
        namespace = self._resolve_namespace_from_provider_data(provider_data)
        self._record_release(namespace=namespace, spec_kind=PROVIDER_API)
        sem = asyncio.Semaphore(_MAX_CONCURRENT_CALLS)

        async def delete(name: str) -> None:
            async with sem:
                await asyncio.to_thread(self._delete, namespace, name)

        results = await asyncio.gather(*(delete(m) for m in machine_ids), return_exceptions=True)
        deleted = [
            m for m, r in zip(machine_ids, results, strict=True) if not isinstance(r, BaseException)
        ]
        failed = [
            (m, str(r))
            for m, r in zip(machine_ids, results, strict=True)
            if isinstance(r, BaseException)
        ]
        for name, error in failed:
            self._logger.warning("SwiftSandbox delete failed: %s/%s: %s", namespace, name, error)
        if failed and not deleted:
            raise RuntimeError(f"All SwiftSandbox deletes failed: {failed[0][1]}")
        return {"deleted": deleted, "failed_deletes": failed}

    # ------------------------------------------------------------------
    # examples
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate

        loop = ["/bin/sh", "-c", "while true; do sleep 30; done"]
        return [
            K8sTemplate(
                template_id="kubeswift-sandbox-pooled",
                name="KubeSwift sandbox from a warm pool",
                description=(
                    "A pull-mode worker in a SwiftSandbox microVM, checked out of the "
                    "SwiftSandboxPool named in metadata.kubeswift.poolRef. Size and "
                    "network come from the pool; machine_image must be the pool's image."
                ),
                provider_type="k8s",
                provider_api=PROVIDER_API,
                machine_image="docker.io/library/alpine:3.20",
                max_machines=10,
                metadata={"kubeswift": {"poolRef": "workers", "command": loop}},
            ),
            K8sTemplate(
                template_id="kubeswift-sandbox-cold",
                name="KubeSwift sandbox, cold boot",
                description="A SwiftSandbox microVM booted on request (no pool).",
                provider_type="k8s",
                provider_api=PROVIDER_API,
                machine_image="docker.io/library/alpine:3.20",
                max_machines=10,
                metadata={
                    "kubeswift": {
                        "cpu": 1,
                        "memory": "512Mi",
                        "network": {"mode": "restricted"},
                        "command": loop,
                    }
                },
            ),
        ]


def register_kubeswift_sandbox_handler() -> None:
    """Add ``KubeSwiftSandbox`` to the Kubernetes provider (idempotent)."""
    register_k8s_handler(
        PROVIDER_API,
        KubeSwiftSandboxHandler,
        custom_resources=[SANDBOX_RESOURCE],
    )


__all__ = [
    "PROVIDER_API",
    "SANDBOX_RESOURCE",
    "KubeSwiftSandboxHandler",
    "register_kubeswift_sandbox_handler",
]
