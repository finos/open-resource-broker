"""In-memory CoreV1 for return-path tests: honours namespaces and equality selectors."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

from kubernetes.client import (
    V1ObjectMeta,
    V1Pod,
    V1PodCondition,
    V1PodList,
    V1PodSpec,
    V1PodStatus,
)
from kubernetes.client.exceptions import ApiException


def _matches(labels: dict[str, str], selector: Optional[str]) -> bool:
    for term in (selector or "").split(","):
        if not term:
            continue
        key, _, value = term.partition("=")
        if labels.get(key) != value:
            return False
    return True


class FakeCoreV1:
    """Stores pods per namespace and records every call as ``(verb, namespace, detail)``."""

    def __init__(self) -> None:
        self.pods: dict[tuple[str, str], V1Pod] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.list_errors: dict[str, BaseException] = {}

    def add_pod(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        *,
        phase: str = "Running",
        ready: bool = True,
        terminating: bool = False,
    ) -> None:
        conditions = [V1PodCondition(type="Ready", status="True" if ready else "False")]
        self.pods[(namespace, name)] = V1Pod(
            metadata=V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels=dict(labels),
                creation_timestamp=datetime.now(timezone.utc),
                deletion_timestamp=datetime.now(timezone.utc) if terminating else None,
            ),
            spec=V1PodSpec(containers=[]),
            status=V1PodStatus(phase=phase, conditions=conditions),
        )

    def list_namespaced_pod(
        self,
        namespace: str,
        label_selector: Optional[str] = None,
        resource_version: Optional[str] = None,
        **_: Any,
    ) -> V1PodList:
        self.calls.append(("list", namespace, label_selector or ""))
        if namespace in self.list_errors:
            raise self.list_errors[namespace]
        return V1PodList(
            items=[
                pod
                for (ns, _name), pod in self.pods.items()
                if ns == namespace and _matches(pod.metadata.labels or {}, label_selector)  # type: ignore[union-attr]
            ]
        )

    def delete_namespaced_pod(self, name: str, namespace: str, **_: Any) -> None:
        self.calls.append(("delete", namespace, name))
        if (namespace, name) not in self.pods:
            raise ApiException(status=404, reason="Not Found")
        del self.pods[(namespace, name)]

    def calls_for(self, verb: str) -> list[tuple[str, str, str]]:
        return [c for c in self.calls if c[0] == verb]


def fake_client(core_v1: FakeCoreV1) -> Any:
    return SimpleNamespace(core_v1=core_v1, apps_v1=SimpleNamespace(), batch_v1=SimpleNamespace())
