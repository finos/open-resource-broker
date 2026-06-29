"""Kubernetes Infrastructure Discovery Service.

Implements the non-interactive discovery flow that feeds ``orb init`` for the
k8s provider.  The interactive prompt loop lives in
:meth:`K8sInfrastructureDiscoveryService.discover_infrastructure_interactive`;
this module provides the full non-interactive leaf-method implementations plus
the composition method :meth:`K8sInfrastructureDiscoveryService.discover_infrastructure`.

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
    from orb.domain.base.ports.console_port import ConsolePort
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


def _is_not_found(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` is a 404 ``ApiException``."""
    try:
        from kubernetes.client.exceptions import ApiException  # noqa: PLC0415
    except ImportError:  # pragma: no cover — extra not installed
        return False
    return isinstance(exc, ApiException) and getattr(exc, "status", None) == 404


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
        console: Optional["ConsolePort"] = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._api_client = api_client
        if console is None:
            from orb.infrastructure.adapters.null_console_adapter import (  # noqa: PLC0415
                NullConsoleAdapter,
            )

            self._console: "ConsolePort" = NullConsoleAdapter()
        else:
            self._console = console

    # ------------------------------------------------------------------
    # Helpers — lazy API client construction
    # ------------------------------------------------------------------

    def _get_api_client(self) -> Any:
        """Return the kubernetes ``ApiClient``, building one on demand from kubeconfig."""
        if self._api_client is not None:
            return self._api_client
        try:
            from kubernetes import config as _k8s_config  # noqa: PLC0415
            from kubernetes.client.api_client import ApiClient  # noqa: PLC0415
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
            # kubernetes-stubs-elephant-fork omits the `configuration`
            # attribute from ApiClient; it exists at runtime.
            host: str = client.configuration.host or "unknown"  # type: ignore[attr-defined]
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

        Drives the six-step prompt sequence:

        1. Detect whether ORB is running in-cluster; ask the operator to confirm.
        2. When out-of-cluster: discover kubeconfig contexts; prompt for selection.
        3. Discover the cluster endpoint (display only — not persisted).
        4. Discover namespaces; prompt for selection (or auto-select on 403).
        5. Discover ServiceAccounts in chosen namespace; prompt for template default.
        6. Discover image pull secrets in chosen namespace; prompt for default.
        7. Probe RBAC; display results to the operator and offer to continue on failure.

        Return shape — only operator-chosen leaves, matching the AWS provider
        pattern where lists and diagnostic scaffolds are shown during prompts
        but never written into config.json:

        Connection-level keys (routed to ``providers[i].config`` by the
        classifier)::

            {
                "in_cluster": bool,              # always present
                "namespace": str,                # always present; chosen namespace
                "context": str,                  # only when out-of-cluster
            }

        Template-default-level keys (routed to ``providers[i].template_defaults``
        by the classifier)::

            {
                "service_account": str,          # only when operator picked one
                "image_pull_secret": str,        # only when operator picked one
            }

        Discovery scaffold (contexts list, cluster endpoint, namespace list,
        service-account list, pull-secret list, RBAC probe dict) are displayed
        to the operator during the wizard but are not included in the returned
        dict.

        Args:
            provider_config: Raw provider config dict (passed through from the
                strategy; only ``"name"`` is currently used, for logging).

        Returns:
            Dict containing only the operator's chosen values as described
            above.  Every key is conditional: the dict may contain as few as
            two keys (``in_cluster`` + ``namespace``) for a minimal in-cluster
            setup.

        Raises:
            K8sDiscoveryError: When the operator aborts due to missing RBAC
                permissions, or when no kubeconfig contexts are available in
                out-of-cluster mode.
        """
        from orb.providers.k8s.services.init_prompts import (  # noqa: PLC0415
            confirm_in_cluster,
            display_rbac_probe,
            pick_context,
            pick_image_pull_secret,
            pick_namespace,
            pick_service_account,
        )

        # ------------------------------------------------------------------
        # Step 1 — In-cluster detection + confirm
        # ------------------------------------------------------------------
        detected_in_cluster = self.detect_in_cluster()
        in_cluster = confirm_in_cluster(self._console, detected_in_cluster)

        # ------------------------------------------------------------------
        # Step 2 — Context selection (out-of-cluster only)
        # ------------------------------------------------------------------
        kubeconfig_path: Optional[Path] = None
        if self._config.kubeconfig_path:
            kubeconfig_path = Path(self._config.kubeconfig_path)

        chosen_context_name: Optional[str] = None

        if not in_cluster:
            all_contexts, current_context = self.discover_contexts(kubeconfig_path=kubeconfig_path)
            chosen_context = pick_context(self._console, all_contexts, current_context)
            chosen_context_name = (
                chosen_context
                if isinstance(chosen_context, str)
                else (chosen_context.name if chosen_context else None)
            )

        # ------------------------------------------------------------------
        # Step 3 — Cluster endpoint (display only — never written to config)
        # ------------------------------------------------------------------
        if not in_cluster:
            effective_context = (
                chosen_context_name
                or self._config.context
                or (current_context.name if current_context is not None else None)  # type: ignore[possibly-undefined]
            )
            cluster_endpoint = self.discover_cluster_endpoint(context=effective_context)
            self._console.info(f"  Cluster endpoint: {cluster_endpoint}")

        # ------------------------------------------------------------------
        # Step 4 — Namespace selection
        # ------------------------------------------------------------------
        # Read SA-bound namespace from the kubelet file (in-cluster fallback).
        sa_bound_ns: Optional[str] = None
        try:
            if _SA_NAMESPACE_FILE.exists():
                sa_bound_ns = _SA_NAMESPACE_FILE.read_text(encoding="utf-8").strip() or None
        except OSError:
            pass

        namespace_infos = self.discover_namespaces()

        if len(namespace_infos) == 1 and sa_bound_ns and namespace_infos[0].name == sa_bound_ns:
            # 403 fallback already applied by discover_namespaces(); auto-select with a notice.
            self._console.info(
                f"  Note: namespace list permission not available;"
                f" using SA-bound namespace '{sa_bound_ns}'"
            )
            chosen_namespace = sa_bound_ns
        elif not namespace_infos and sa_bound_ns:
            self._console.info(
                f"  Note: namespace list permission not available;"
                f" using SA-bound namespace '{sa_bound_ns}'"
            )
            chosen_namespace = sa_bound_ns
        else:
            # Compute default: config value → SA-bound → first active → "default"
            config_ns = self._config.namespace or ""
            if config_ns and config_ns != "default":
                fallback = config_ns
            elif sa_bound_ns:
                fallback = sa_bound_ns
            else:
                active = [n for n in namespace_infos if n.status in ("Active", "active")]
                fallback = active[0].name if active else "default"

            chosen_namespace = pick_namespace(self._console, namespace_infos, fallback)

        # ------------------------------------------------------------------
        # Step 5 — ServiceAccount selection
        # ------------------------------------------------------------------
        sa_infos = self.discover_service_accounts(namespace=chosen_namespace)
        if not sa_infos:
            self._console.info(
                "  Note: could not list ServiceAccounts — you can set"
                " `service_account` in your template later."
            )
            chosen_sa: Optional[str] = None
        else:
            raw_sa = pick_service_account(self._console, sa_infos, default="default")
            chosen_sa = raw_sa or None

        # ------------------------------------------------------------------
        # Step 6 — Image pull secret selection
        # ------------------------------------------------------------------
        pull_secret_names = self.discover_image_pull_secrets(namespace=chosen_namespace)
        if not pull_secret_names:
            self._console.info("  Note: no image pull secrets found in namespace.")
        chosen_pull_secret: Optional[str] = pick_image_pull_secret(self._console, pull_secret_names)

        # ------------------------------------------------------------------
        # Step 7 — RBAC probe + display verdict; confirm on failure
        # ------------------------------------------------------------------
        try:
            rbac = self.probe_rbac(namespace=chosen_namespace)
        except K8sDiscoveryError as exc:
            self._logger.warning("discover_infrastructure_interactive: RBAC probe failed: %s", exc)
            rbac = RBACProbeResult(
                namespace=chosen_namespace,
                can_create_pods=False,
                can_watch_pods=False,
                can_delete_pods=False,
            )

        # Display the RBAC verdict to the operator.  The probe result itself is
        # never written to config — it is diagnostic information only.
        should_continue = display_rbac_probe(
            self._console,
            rbac,
            namespace=chosen_namespace,
            sa=chosen_sa or None,
        )
        if not should_continue:
            raise K8sDiscoveryError("Operator aborted orb init due to missing RBAC permissions.")

        # ------------------------------------------------------------------
        # Build the return dict — only operator-chosen leaves, no scaffold
        # ------------------------------------------------------------------
        result: dict[str, Any] = {
            "in_cluster": in_cluster,
            "namespace": chosen_namespace,
        }
        # context is only meaningful (and only discovered) when out-of-cluster
        if not in_cluster and chosen_context_name is not None:
            result["context"] = chosen_context_name
        # template-default-level keys — only present when the operator picked a value
        if chosen_sa is not None:
            result["service_account"] = chosen_sa
        if chosen_pull_secret is not None:
            result["image_pull_secret"] = chosen_pull_secret
        return result

    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate that a configured K8s provider can reach its cluster.

        Performs five checks in order:

        1. **API server reachable** — ``CoreV1Api.get_api_resources()`` with a
           5-second timeout.  Any exception is treated as an unreachable server.
        2. **Context exists** — ``kubernetes.config.list_kube_config_contexts()``
           is used to verify the configured context is present in the kubeconfig.
           Skipped when running in-cluster (contexts are irrelevant in-cluster).
        3. **Namespace exists** — ``CoreV1Api.read_namespace(name=ns)`` is called
           to confirm the target namespace is present in the cluster.  A 404
           ``ApiException`` is reported as a missing-namespace issue.
        4. **ServiceAccount exists** — when a ``service_account`` is configured
           (via ``provider_config["template_defaults"]["service_account"]`` or
           the provider-level ``config["service_account"]``), checks that the
           named ServiceAccount exists in the target namespace via
           ``CoreV1Api.read_namespaced_service_account``.
        5. **RBAC probe** — re-runs :meth:`probe_rbac` on the target namespace
           and reports any denied verb as an issue.

        Args:
            provider_config: Raw provider config dict with optional
                ``"name"``, ``"config"``, and ``"template_defaults"`` keys.

        Returns:
            ``{"provider": str, "valid": bool, "issues": list[str]}``
        """
        provider_name: str = provider_config.get("name", "")
        instance_cfg: dict[str, Any] = provider_config.get("config", {}) or {}
        template_defaults: dict[str, Any] = provider_config.get("template_defaults", {}) or {}
        issues: list[str] = []

        # Resolve the effective namespace to validate against.
        # Priority: provider_config["config"]["namespace"] → K8sProviderConfig.namespace
        namespace: str = instance_cfg.get("namespace") or self._config.namespace or "default"

        # Resolve the configured context (out-of-cluster only).
        context: Optional[str] = instance_cfg.get("context") or self._config.context

        # Resolve whether we are in-cluster.
        in_cluster_flag: Optional[bool] = instance_cfg.get("in_cluster")
        if in_cluster_flag is None:
            in_cluster_flag = self._config.in_cluster
        if in_cluster_flag is None:
            in_cluster_flag = is_in_cluster()

        # Derive the cluster endpoint for use in error messages.
        cluster_endpoint: str = self.discover_cluster_endpoint(context=context)

        # ------------------------------------------------------------------
        # Check 1 — API server reachable
        # ------------------------------------------------------------------
        api_reachable = True
        try:
            # Apply a tight 5-second timeout to avoid hanging on unreachable clusters.
            self._core_v1().get_api_resources(_request_timeout=5)
        except K8sError:
            raise
        except Exception as exc:  # noqa: BLE001
            issues.append(f"Apiserver unreachable at {cluster_endpoint}: {exc}")
            api_reachable = False

        # Checks 2–5 are skipped when the API server itself is not reachable;
        # they would all generate redundant noise.
        if api_reachable:
            # ------------------------------------------------------------------
            # Check 2 — Configured context exists in kubeconfig (out-of-cluster only)
            # ------------------------------------------------------------------
            if not in_cluster_flag and context is not None:
                try:
                    from kubernetes import config as _k8s_config  # noqa: PLC0415

                    config_file = (
                        str(self._config.kubeconfig_path) if self._config.kubeconfig_path else None
                    )
                    raw_contexts, _ = _k8s_config.list_kube_config_contexts(config_file=config_file)
                    known_names: list[str] = [
                        str(dict(c).get("name") or "") for c in (raw_contexts or [])
                    ]
                    if context not in known_names:
                        issues.append(f"Configured context '{context}' not found in kubeconfig")
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "validate_infrastructure: could not list kubeconfig contexts: %s", exc
                    )

            # ------------------------------------------------------------------
            # Check 3 — Namespace exists in cluster
            # ------------------------------------------------------------------
            try:
                self._core_v1().read_namespace(name=namespace)
            except K8sError:
                raise
            except Exception:  # noqa: BLE001
                issues.append(f"Namespace '{namespace}' not found in cluster")

            # ------------------------------------------------------------------
            # Check 4 — ServiceAccount exists (when one is configured)
            # ------------------------------------------------------------------
            service_account: Optional[str] = template_defaults.get(
                "service_account"
            ) or instance_cfg.get("service_account")
            if service_account:
                try:
                    self._core_v1().read_namespaced_service_account(
                        name=service_account, namespace=namespace
                    )
                except K8sError:
                    raise
                except Exception:  # noqa: BLE001
                    issues.append(
                        f"ServiceAccount '{service_account}' not found in namespace '{namespace}'"
                    )

            # ------------------------------------------------------------------
            # Check 5 — RBAC probe
            # ------------------------------------------------------------------
            try:
                rbac = self.probe_rbac(namespace=namespace)
                for verb, allowed in (
                    ("create", rbac.can_create_pods),
                    ("watch", rbac.can_watch_pods),
                    ("delete", rbac.can_delete_pods),
                ):
                    if not allowed:
                        issues.append(f"Missing permission: pods.{verb} in namespace {namespace}")
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("validate_infrastructure: RBAC probe failed: %s", exc)
                issues.append(f"RBAC probe failed: {exc}")

        return {
            "provider": provider_name,
            "valid": len(issues) == 0,
            "issues": issues,
        }
