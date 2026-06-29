"""Kubernetes Infrastructure Discovery Service.

Implements the non-interactive and interactive discovery flows that feed
``orb init`` for the k8s provider.  The real leaf-method implementations
live in Phase B; this module provides the skeleton with stub bodies so
that :class:`K8sProviderStrategy` can satisfy
:class:`~orb.domain.base.ports.provider_discovery_port.ProviderDiscoveryPort`
immediately.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.providers.k8s.services.discovery_models import (
    KubeContextInfo,
    NamespaceInfo,
    RBACProbeResult,
    ServiceAccountInfo,
)

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.providers.k8s.configuration.config import K8sProviderConfig


class K8sInfrastructureDiscoveryService:
    """Discovery service for Kubernetes provider infrastructure.

    Constructor arguments mirror the AWS counterpart so the strategy can
    construct the service identically via the lazy-getter pattern.

    Args:
        config: K8s provider configuration for the target cluster.
        logger: Injected logging port — never use ``logging.getLogger``
            directly inside this class.
        api_client: Optional pre-built kubernetes ``ApiClient`` (injected
            in unit tests to avoid real cluster connections).
    """

    def __init__(
        self,
        config: "K8sProviderConfig",
        logger: "LoggingPort",
        api_client: Optional[Any] = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._api_client = api_client

    # ------------------------------------------------------------------
    # Leaf methods — Phase B implements these; Phase A stubs return empty.
    # ------------------------------------------------------------------

    def detect_in_cluster(self) -> bool:
        """Detect whether ORB is running inside a Kubernetes pod.

        Delegates to :func:`orb.providers.k8s.auth.in_cluster.is_in_cluster`.
        Returns ``False`` until Phase B wires the real implementation.
        """
        return False

    def discover_contexts(self) -> list[KubeContextInfo]:
        """Return all kubeconfig contexts available in the local kubeconfig.

        Parses the kubeconfig file via ``kubernetes.config.list_kube_config_contexts``
        with no live network call.  Returns an empty list until Phase B.
        """
        return []

    def discover_cluster_endpoint(self, context: Optional[str] = None) -> str:
        """Return the API-server URL for ``context`` (display only).

        Reads the kubeconfig file; no live network call.  Returns
        ``"unknown"`` until Phase B.
        """
        return "unknown"

    def discover_namespaces(self) -> list[NamespaceInfo]:
        """Return all accessible namespaces from the target cluster.

        Falls back to the SA-bound namespace on 403.  Returns an empty
        list until Phase B.
        """
        return []

    def discover_service_accounts(self, namespace: str) -> list[ServiceAccountInfo]:
        """Return ServiceAccounts in ``namespace``.

        Returns an empty list on 403 or until Phase B.
        """
        return []

    def discover_image_pull_secrets(self, namespace: str) -> list[str]:
        """Return docker-registry secret names in ``namespace``.

        Secret values are never surfaced — only ``.metadata.name`` is
        returned.  Returns an empty list on 403 or until Phase B.
        """
        return []

    def probe_rbac(self, namespace: str) -> RBACProbeResult:
        """Probe whether the current identity may create/watch/delete pods.

        Runs three ``SelfSubjectAccessReview`` calls.  Returns a
        result with all permissions denied until Phase B.
        """
        return RBACProbeResult(
            namespace=namespace,
            can_create_pods=False,
            can_watch_pods=False,
            can_delete_pods=False,
        )

    # ------------------------------------------------------------------
    # Composition methods
    # ------------------------------------------------------------------

    def discover_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Non-interactive infrastructure discovery.

        Returns the full discovery dict shaped for ``K8sProviderConfig``
        population.  Returns an empty scaffold until Phase B.
        """
        provider_name: str = provider_config.get("name", "")
        return {
            "in_cluster": False,
            "contexts": [],
            "current_context": None,
            "cluster_endpoint": "unknown",
            "namespaces": [],
            "default_namespace": self._config.namespace or "default",
            "service_accounts": [],
            "image_pull_secrets": [],
            "rbac_probe": {
                "create_pods": False,
                "watch_pods": False,
                "delete_pods": False,
            },
            "provider": provider_name,
        }

    def discover_infrastructure_interactive(
        self, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Interactive prompt-driven infrastructure discovery.

        Drives the six-step prompt sequence documented in the API design.
        Returns an empty scaffold until Phase C.
        """
        return self.discover_infrastructure(provider_config)

    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate that a configured K8s provider can reach its cluster.

        Returns a valid scaffold (no issues) until Phase D.
        """
        provider_name: str = provider_config.get("name", "")
        return {
            "provider": provider_name,
            "valid": True,
            "issues": [],
        }
