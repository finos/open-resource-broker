"""Kubernetes Infrastructure Discovery Service.

Implements the non-interactive discovery flow that feeds ``orb init`` for the
k8s provider.  The interactive prompt loop lives in Phase C; this module
provides the full non-interactive leaf-method implementations plus the
composition method :meth:`K8sInfrastructureDiscoveryService.discover_infrastructure`.

Public leaf methods (all non-interactive, all safe to call without a live
cluster in unit tests when the ``api_client`` constructor argument is
supplied):

* :meth:`detect_in_cluster` — filesystem sentinel check, no HTTP.
* :meth:`discover_contexts` — kubeconfig file parse, no HTTP.
* :meth:`discover_cluster_endpoint` — kubeconfig file read, no HTTP.
* :meth:`discover_namespaces` — ``CoreV1Api.list_namespace`` + 403 fallback.
* :meth:`discover_service_accounts` — ``CoreV1Api.list_namespaced_service_account``.
* :meth:`discover_image_pull_secrets` — ``CoreV1Api.list_namespaced_secret``.
* :meth:`probe_rbac` — three ``AuthorizationV1Api.create_self_subject_access_review`` calls.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from orb.providers.k8s.auth.in_cluster import is_in_cluster
from orb.providers.k8s.exceptions.k8s_errors import K8sDiscoveryError, K8sError
from orb.providers.k8s.services.discovery_models import (
    KubeContextInfo,
    NamespaceInfo,
    RBACProbeResult,
    ServiceAccountInfo,
)

if TYPE_CHECKING:
    from orb.domain.base.ports import LoggingPort
    from orb.providers.k8s.configuration.config import K8sProviderConfig

# Kubernetes kubelet writes the pod's own namespace here.
_SA_NAMESPACE_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")


def _age_days(creation_timestamp: Any) -> int:
    """Return the integer age in whole days for a ``V1ObjectMeta.creation_timestamp``.

    The kubernetes Python client may return the timestamp as a
    :class:`datetime.datetime` (when ``_preload_content=True``, the default)
    or as an ISO 8601 string.  Both cases are handled.  Returns ``0`` when
    the timestamp is absent or unparseable.
    """
    if creation_timestamp is None:
        return 0
    try:
        if isinstance(creation_timestamp, datetime.datetime):
            ts = creation_timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
        else:
            ts_str = str(creation_timestamp).replace("Z", "+00:00")
            ts = datetime.datetime.fromisoformat(ts_str)
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        return max(0, int((now - ts).total_seconds() / 86400))
    except (ValueError, TypeError, OSError):
        return 0


def _is_forbidden(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` is a 403 ``ApiException``."""
    try:
        from kubernetes.client.exceptions import ApiException  # noqa: PLC0415
    except ImportError:  # pragma: no cover — extra not installed
        return False
    return isinstance(exc, ApiException) and getattr(exc, "status", None) == 403


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
    # Helpers — lazy API client construction
    # ------------------------------------------------------------------

    def _get_api_client(self) -> Any:
        """Return the kubernetes ``ApiClient``, building one on demand from kubeconfig."""
        if self._api_client is not None:
            return self._api_client
        try:
            from kubernetes import config as _k8s_config  # noqa: PLC0415
            from kubernetes.client import ApiClient  # noqa: PLC0415
        except ImportError as exc:
            raise K8sError(
                "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
            ) from exc
        if is_in_cluster():
            _k8s_config.load_incluster_config()
        else:
            _k8s_config.load_kube_config(
                config_file=self._config.kubeconfig_path,
                context=self._config.context,
            )
        return ApiClient()

    def _core_v1(self) -> Any:
        """Return a ``CoreV1Api`` instance backed by this service's client."""
        try:
            from kubernetes.client import CoreV1Api  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover — extra not installed
            raise K8sError(
                "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
            ) from exc
        return CoreV1Api(self._get_api_client())

    def _auth_v1(self) -> Any:
        """Return an ``AuthorizationV1Api`` instance backed by this service's client."""
        try:
            from kubernetes.client import AuthorizationV1Api  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover — extra not installed
            raise K8sError(
                "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
            ) from exc
        return AuthorizationV1Api(self._get_api_client())

    # ------------------------------------------------------------------
    # Leaf methods
    # ------------------------------------------------------------------

    def detect_in_cluster(self) -> bool:
        """Detect whether ORB is running inside a Kubernetes pod.

        Delegates to :func:`orb.providers.k8s.auth.in_cluster.is_in_cluster`.
        The in-cluster sentinel is the ``/var/run/secrets/kubernetes.io``
        directory written by the kubelet for every pod that has a ServiceAccount
        mount.

        Returns:
            ``True`` when the in-cluster sentinel is present; ``False`` otherwise.
        """
        return is_in_cluster()

    def discover_contexts(
        self, kubeconfig_path: Optional[Path] = None
    ) -> tuple[list[KubeContextInfo], Optional[KubeContextInfo]]:
        """Return all kubeconfig contexts and the current (active) context.

        Parses the kubeconfig file via
        ``kubernetes.config.list_kube_config_contexts`` — a pure YAML parse
        with no live network call.

        Args:
            kubeconfig_path: Path to a specific kubeconfig file.  When
                ``None``, the kubernetes client falls back to the
                ``KUBECONFIG`` env var and then ``~/.kube/config``.

        Returns:
            A two-tuple ``(all_contexts, current_context)`` where
            ``all_contexts`` is a list of :class:`KubeContextInfo` (may be
            empty) and ``current_context`` is the active context or ``None``
            when no current context is set.

        Raises:
            K8sDiscoveryError: When the kubernetes SDK is not installed.
        """
        try:
            from kubernetes import config as _k8s_config  # noqa: PLC0415
        except ImportError as exc:
            raise K8sDiscoveryError(
                "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
            ) from exc

        config_file = str(kubeconfig_path) if kubeconfig_path is not None else None
        try:
            raw_contexts, raw_current = _k8s_config.list_kube_config_contexts(
                config_file=config_file
            )
        except Exception as exc:  # noqa: BLE001 — FileNotFoundError, yaml errors, etc.
            self._logger.warning(
                "discover_contexts: failed to parse kubeconfig (%s=%r): %s",
                "config_file",
                config_file,
                exc,
            )
            return [], None

        current_name: Optional[str] = None
        if raw_current:
            current_name = raw_current.get("name")

        def _parse(raw: dict[str, Any]) -> KubeContextInfo:
            name: str = raw.get("name", "")
            ctx: dict[str, Any] = raw.get("context", {}) or {}
            return KubeContextInfo(
                name=name,
                cluster=ctx.get("cluster", ""),
                user=ctx.get("user", ""),
                namespace=ctx.get("namespace") or None,
                is_current=(name == current_name),
            )

        all_contexts: list[KubeContextInfo] = [
            _parse(dict(r))  # type: ignore[arg-type]
            for r in (raw_contexts or [])
        ]
        current_ctx: Optional[KubeContextInfo] = next(
            (c for c in all_contexts if c.is_current), None
        )
        return all_contexts, current_ctx

    def discover_cluster_endpoint(self, context: Optional[str] = None) -> str:
        """Return the API-server URL for the given kubeconfig context.

        Reads the kubeconfig file to extract the cluster server URL — no live
        network call is made.  The URL is for display purposes only and is
        never written into ``K8sProviderConfig``.

        Args:
            context: kubeconfig context name.  When ``None`` the active
                context is used.

        Returns:
            The apiserver URL (e.g. ``"https://1.2.3.4:6443"``).  Falls back
            to ``"unknown"`` when the URL cannot be resolved.
        """
        try:
            from kubernetes import config as _k8s_config  # noqa: PLC0415
        except ImportError:
            self._logger.warning("discover_cluster_endpoint: kubernetes SDK not installed.")
            return "unknown"

        try:
            client = _k8s_config.new_client_from_config(context=context)
            host: str = client.configuration.host or "unknown"
            return host
        except Exception as exc:  # noqa: BLE001 — ConfigException, etc.
            self._logger.warning(
                "discover_cluster_endpoint: could not resolve endpoint for context=%r: %s",
                context,
                exc,
            )
            return "unknown"

    def discover_namespaces(self) -> list[NamespaceInfo]:
        """Return all accessible namespaces in the target cluster.

        Uses ``CoreV1Api.list_namespace`` to fetch the full namespace list.

        **403 fallback** (critical for in-cluster operation): most namespace-scoped
        ServiceAccounts lack the cluster-scoped ``namespaces/list`` RBAC grant.
        When a 403 is received this method falls back to reading the SA-bound
        namespace from the kubelet-written file at
        ``/var/run/secrets/kubernetes.io/serviceaccount/namespace`` and returns a
        single-element list containing that namespace with ``status="Active"``.

        When neither the API call nor the fallback file are available (out-of-cluster
        403), a warning is logged and an empty list is returned.

        Returns:
            A list of :class:`NamespaceInfo` objects.  May be empty when
            permissions are insufficient and the SA namespace file is absent.
        """
        try:
            core = self._core_v1()
            ns_list = core.list_namespace()
        except K8sError:
            raise
        except Exception as exc:  # noqa: BLE001
            if _is_forbidden(exc):
                return self._fallback_namespaces_from_sa_file()
            raise K8sDiscoveryError(f"Failed to list namespaces: {exc}") from exc

        result: list[NamespaceInfo] = []
        for ns in ns_list.items:
            meta = ns.metadata or {}
            status = (ns.status.phase or "Unknown") if ns.status else "Unknown"
            name: str = getattr(meta, "name", "") or ""
            labels: dict[str, str] = dict(getattr(meta, "labels", None) or {})
            creation_ts = getattr(meta, "creation_timestamp", None)
            result.append(
                NamespaceInfo(
                    name=name,
                    status=status,
                    age_days=_age_days(creation_ts),
                    labels=labels,
                )
            )
        return result

    def _fallback_namespaces_from_sa_file(self) -> list[NamespaceInfo]:
        """Return the SA-bound namespace from the kubelet file, or empty list."""
        try:
            if _SA_NAMESPACE_FILE.exists():
                ns_name = _SA_NAMESPACE_FILE.read_text(encoding="utf-8").strip()
                if ns_name:
                    self._logger.debug(
                        "discover_namespaces: 403 from API; falling back to SA-bound namespace %r.",
                        ns_name,
                    )
                    return [NamespaceInfo(name=ns_name, status="Active", age_days=0, labels={})]
        except OSError as exc:
            self._logger.warning(
                "discover_namespaces: 403 from API and could not read SA namespace file: %s",
                exc,
            )
        self._logger.warning(
            "discover_namespaces: 403 from API; SA namespace file absent or unreadable. "
            "Returning empty namespace list.",
        )
        return []

    def discover_service_accounts(self, namespace: str) -> list[ServiceAccountInfo]:
        """Return ServiceAccounts in ``namespace``.

        Uses ``CoreV1Api.list_namespaced_service_account``.

        On 403 (missing ``serviceaccounts/list`` RBAC), returns an empty list
        with a warning log so the caller can skip the SA selection step.

        Args:
            namespace: Kubernetes namespace to query.

        Returns:
            A list of :class:`ServiceAccountInfo` objects, or an empty list on
            permission errors.
        """
        try:
            core = self._core_v1()
            sa_list = core.list_namespaced_service_account(namespace)
        except K8sError:
            raise
        except Exception as exc:  # noqa: BLE001
            if _is_forbidden(exc):
                self._logger.warning(
                    "discover_service_accounts: 403 from namespace=%r; "
                    "skipping ServiceAccount discovery.",
                    namespace,
                )
                return []
            raise K8sDiscoveryError(
                f"Failed to list ServiceAccounts in namespace {namespace!r}: {exc}"
            ) from exc

        result: list[ServiceAccountInfo] = []
        for sa in sa_list.items:
            meta = sa.metadata or {}
            name: str = getattr(meta, "name", "") or ""
            annotations: dict[str, str] = dict(getattr(meta, "annotations", None) or {})
            secrets_count: int = len(sa.secrets or [])
            result.append(
                ServiceAccountInfo(
                    name=name,
                    namespace=namespace,
                    secrets_count=secrets_count,
                    annotations=annotations,
                )
            )
        return result

    def discover_image_pull_secrets(self, namespace: str) -> list[str]:
        """Return docker-registry secret names in ``namespace``.

        Uses ``CoreV1Api.list_namespaced_secret`` with
        ``field_selector="type=kubernetes.io/dockerconfigjson"`` to restrict
        the query to image-pull secrets only.

        Secret values are intentionally **never** read or surfaced — only
        ``.metadata.name`` is accessed.

        On 403 (missing ``secrets/list`` RBAC), returns an empty list.

        Args:
            namespace: Kubernetes namespace to query.

        Returns:
            A list of secret names (strings only).  Empty on permission errors
            or when no docker-registry secrets exist.
        """
        try:
            core = self._core_v1()
            secret_list = core.list_namespaced_secret(
                namespace,
                field_selector="type=kubernetes.io/dockerconfigjson",
            )
        except K8sError:
            raise
        except Exception as exc:  # noqa: BLE001
            if _is_forbidden(exc):
                self._logger.warning(
                    "discover_image_pull_secrets: 403 from namespace=%r; "
                    "skipping image pull secret discovery.",
                    namespace,
                )
                return []
            raise K8sDiscoveryError(
                f"Failed to list image pull secrets in namespace {namespace!r}: {exc}"
            ) from exc

        return [
            (secret.metadata.name or "")
            for secret in secret_list.items
            if secret.metadata and secret.metadata.name
        ]

    def probe_rbac(self, namespace: str) -> RBACProbeResult:
        """Probe whether the current identity may create, watch, and delete pods.

        Issues three ``SelfSubjectAccessReview`` calls (one per verb: create,
        watch, delete) against ``resource=pods`` in ``namespace``.  The reviews
        test the identity of the calling process — the operator running
        ``orb init`` out-of-cluster, or the SA token in-cluster — not a
        separately configured identity.

        Args:
            namespace: Kubernetes namespace to probe.

        Returns:
            A :class:`RBACProbeResult` with per-verb boolean flags.

        Raises:
            K8sDiscoveryError: When the ``SelfSubjectAccessReview`` API itself
                returns an error (extremely rare; indicates cluster policy blocks
                self-review).
        """
        try:
            from kubernetes.client import (  # noqa: PLC0415
                AuthorizationV1Api,
                V1ResourceAttributes,
                V1SelfSubjectAccessReview,
                V1SelfSubjectAccessReviewSpec,
            )
        except ImportError as exc:
            raise K8sDiscoveryError(
                "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
            ) from exc

        auth = AuthorizationV1Api(self._get_api_client())
        results: dict[str, bool] = {}

        for verb in ("create", "watch", "delete"):
            body = V1SelfSubjectAccessReview(
                spec=V1SelfSubjectAccessReviewSpec(
                    resource_attributes=V1ResourceAttributes(
                        namespace=namespace,
                        verb=verb,
                        resource="pods",
                    )
                )
            )
            try:
                response = auth.create_self_subject_access_review(body)
                resp_status = getattr(response, "status", None)
                allowed: bool = bool(getattr(resp_status, "allowed", False))
            except Exception as exc:  # noqa: BLE001
                raise K8sDiscoveryError(
                    f"SelfSubjectAccessReview for verb={verb!r} in namespace={namespace!r} "
                    f"failed: {exc}"
                ) from exc
            results[verb] = allowed

        return RBACProbeResult(
            namespace=namespace,
            can_create_pods=results.get("create", False),
            can_watch_pods=results.get("watch", False),
            can_delete_pods=results.get("delete", False),
        )

    # ------------------------------------------------------------------
    # Composition method
    # ------------------------------------------------------------------

    def discover_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Non-interactive infrastructure discovery.

        Composes the leaf methods to produce the full discovery dict shaped
        for ``K8sProviderConfig`` population.  The composition follows the
        same field-routing contract as the AWS counterpart:

        * ``in_cluster``, ``context``, ``default_image_pull_secret`` →
          ``provider_instance.config``
        * ``namespace`` → ``provider_instance.config.namespace``
        * ``service_account`` suggestions → ``provider_instance.template_defaults``

        Args:
            provider_config: Raw provider config dict (passed through from
                ``K8sProviderStrategy.discover_infrastructure``).  The
                ``"name"`` key is used for the ``"provider"`` field in the
                return dict.

        Returns:
            Discovery dict with keys:
            ``in_cluster``, ``contexts``, ``current_context``,
            ``cluster_endpoint``, ``namespaces``, ``default_namespace``,
            ``service_accounts``, ``image_pull_secrets``, ``rbac_probe``,
            ``provider``.
        """
        provider_name: str = provider_config.get("name", "")

        # --- Auth / cluster ---
        in_cluster = self.detect_in_cluster()

        kubeconfig_path: Optional[Path] = None
        if self._config.kubeconfig_path:
            kubeconfig_path = Path(self._config.kubeconfig_path)

        all_contexts, current_context = self.discover_contexts(kubeconfig_path=kubeconfig_path)
        context_names: list[str] = [c.name for c in all_contexts]
        current_context_name: Optional[str] = (
            current_context.name if current_context is not None else None
        )

        # Use configured context (or the active one from kubeconfig) for endpoint.
        effective_context = self._config.context or current_context_name
        cluster_endpoint = self.discover_cluster_endpoint(context=effective_context)

        # --- Namespacing ---
        namespace_infos = self.discover_namespaces()
        namespace_names: list[str] = [n.name for n in namespace_infos]

        # Resolve default namespace: prefer the config value, then the SA
        # token file (in-cluster), then the first active namespace, then "default".
        default_namespace: str = self._config.namespace or "default"
        if not default_namespace or default_namespace == "default":
            # Try to do better from discovery results.
            if in_cluster:
                try:
                    if _SA_NAMESPACE_FILE.exists():
                        sa_ns = _SA_NAMESPACE_FILE.read_text(encoding="utf-8").strip()
                        if sa_ns:
                            default_namespace = sa_ns
                except OSError:
                    pass
            if default_namespace == "default" and namespace_names:
                active = [n for n in namespace_infos if n.status in ("Active", "active")]
                if active:
                    default_namespace = active[0].name

        # --- Per-namespace resources (use the resolved default namespace) ---
        sa_infos = self.discover_service_accounts(namespace=default_namespace)
        sa_names: list[str] = [sa.name for sa in sa_infos]

        pull_secrets = self.discover_image_pull_secrets(namespace=default_namespace)

        # --- RBAC probe ---
        try:
            rbac = self.probe_rbac(namespace=default_namespace)
            rbac_probe: dict[str, bool] = {
                "create_pods": rbac.can_create_pods,
                "watch_pods": rbac.can_watch_pods,
                "delete_pods": rbac.can_delete_pods,
            }
        except K8sDiscoveryError as exc:
            self._logger.warning("discover_infrastructure: RBAC probe failed: %s", exc)
            rbac_probe = {
                "create_pods": False,
                "watch_pods": False,
                "delete_pods": False,
            }

        return {
            "in_cluster": in_cluster,
            "contexts": context_names,
            "current_context": current_context_name,
            "cluster_endpoint": cluster_endpoint,
            "namespaces": namespace_names,
            "default_namespace": default_namespace,
            "service_accounts": sa_names,
            "image_pull_secrets": pull_secrets,
            "rbac_probe": rbac_probe,
            "provider": provider_name,
        }

    def discover_infrastructure_interactive(
        self, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Interactive prompt-driven infrastructure discovery.

        Drives the six-step prompt sequence documented in the API design.
        Phase C replaces this stub with the real interactive implementation;
        for now it delegates to :meth:`discover_infrastructure` so the
        non-interactive path is exercised during ``orb init --non-interactive``.
        """
        return self.discover_infrastructure(provider_config)

    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate that a configured K8s provider can reach its cluster.

        Returns a valid scaffold (no issues) until Phase D implements the
        real validation checks.
        """
        provider_name: str = provider_config.get("name", "")
        return {
            "provider": provider_name,
            "valid": True,
            "issues": [],
        }
