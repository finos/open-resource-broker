"""Data models for Kubernetes infrastructure discovery.

All four dataclasses are pure data carriers with no kubernetes SDK
dependency so they can be used in unit tests without any cluster or
mock-API setup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NamespaceInfo:
    """Kubernetes namespace metadata returned by :meth:`discover_namespaces`."""

    name: str
    status: str  # "Active" | "Terminating"
    age_days: int
    labels: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.name} [{self.status}]"


@dataclass
class ServiceAccountInfo:
    """Kubernetes ServiceAccount metadata returned by :meth:`discover_service_accounts`."""

    name: str
    namespace: str
    secrets_count: int
    annotations: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.name}/{self.namespace} (secrets={self.secrets_count})"


@dataclass
class KubeContextInfo:
    """Kubeconfig context entry returned by :meth:`discover_contexts`."""

    name: str
    cluster: str
    user: str
    namespace: Optional[str]  # default namespace recorded in context, if any
    is_current: bool

    def __str__(self) -> str:
        current_marker = " [current]" if self.is_current else ""
        return f"{self.name} -> {self.cluster}{current_marker}"


@dataclass
class RBACProbeResult:
    """Result of a three-verb RBAC self-review for pod operations."""

    namespace: str
    can_create_pods: bool
    can_watch_pods: bool
    can_delete_pods: bool

    @property
    def all_granted(self) -> bool:
        """Return ``True`` when all three pod verbs are permitted."""
        return self.can_create_pods and self.can_watch_pods and self.can_delete_pods

    def __str__(self) -> str:
        def _tick(ok: bool) -> str:
            return "granted" if ok else "DENIED"

        return (
            f"RBAC({self.namespace}): "
            f"create={_tick(self.can_create_pods)}, "
            f"watch={_tick(self.can_watch_pods)}, "
            f"delete={_tick(self.can_delete_pods)}"
        )
