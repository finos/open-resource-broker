"""Kubernetes provider configuration.

Single source of truth for the k8s provider.  ``BaseSettings`` with an
``ORB_K8S_`` env-var prefix plus a ``BaseProviderConfig`` mixin so the model
integrates with the configuration loader and the provider settings registry.
"""

from __future__ import annotations

import contextlib
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from orb.infrastructure.interfaces.provider import BaseProviderConfig
from orb.infrastructure.logging.logger import get_logger
from orb.providers.k8s.utilities.dns_names import (
    DNS_1123_LABEL_REGEX as _DNS_1123_LABEL_RE,
    DNS_1123_SUBDOMAIN_REGEX as _DNS_SUBDOMAIN_RE,
)

_SA_NAMESPACE_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")

# Symphony-era field names that were renamed when the k8s provider was
# introduced.  Keys are the legacy names; values are the canonical names.
_LEGACY_FIELD_MAP: dict[str, str] = {
    "kube_config_path": "kubeconfig_path",
    "kube_context": "context",
    "default_namespace": "namespace",
    "pod_timeout": "pod_timeout_seconds",
    "orphan_gc_interval": "orphan_gc_interval_seconds",
    "orphan_min_age": "orphan_min_age_seconds",
}


def _get_logger() -> Any:
    """Return a logger for namespace auto-detection messages.

    The K8sProviderConfig model validator runs during Pydantic construction,
    before any DI container is available, so injecting a LoggingPort here is
    not feasible without a service-locator.  The project ``get_logger``
    wrapper (a thin alias over ``logging.getLogger``) is used instead of bare
    ``logging.getLogger`` so the call follows the project-wide logging
    convention.

    TODO: move namespace auto-detection out of the validator and into the
    provider strategy's initialize() path so the injected LoggingPort can
    be used instead.
    """
    return get_logger(__name__)


def _read_in_cluster_namespace() -> Optional[str]:
    """Return the namespace from the ServiceAccount token file, or ``None``.

    Reads ``/var/run/secrets/kubernetes.io/serviceaccount/namespace`` when
    ORB is running inside a Kubernetes pod.  Returns the trimmed content, or
    ``None`` if the file is absent or unreadable (out-of-cluster case).
    """
    with contextlib.suppress(OSError):
        if _SA_NAMESPACE_FILE.exists():
            ns = _SA_NAMESPACE_FILE.read_text(encoding="utf-8").strip()
            if ns:
                return ns
    return None


class K8sNamingConfig(BaseModel):
    """Configurable resource-naming policy for managed Kubernetes workloads.

    All four controller kinds (Pod, Deployment, StatefulSet, Job) use the
    pattern ``<prefix>-<uuid_segment>`` for the resource name, where
    ``uuid_segment`` is the first ``uuid_chars`` hex characters of the
    request UUID (hyphens stripped).  Pods additionally append a
    zero-padded sequential suffix ``-<seq:04d>`` so each pod in a batch
    gets a unique name.

    Budget math (must pass the model validator):

    * **Deployment** (tightest): pod names inherit the deployment name as a
      prefix and the ReplicaSet controller appends ``-<hash>-<suffix>``
      (≈16 chars).  So the deployment name must satisfy
      ``len(prefix) + 1 + uuid_chars ≤ max_deployment_name_len``.
    * **StatefulSet**: pod names are ``<statefulset-name>-<ordinal>``; the
      ordinal can be up to 5 digits for very large sets, so the
      statefulset name must leave room: ``len(prefix) + 1 + uuid_chars ≤
      max_statefulset_name_len``.
    * **Job**: the Job controller may append a controller-suffix;
      ``len(prefix) + 1 + uuid_chars ≤ max_job_name_len``.
    * **Pod**: ``len(prefix) + 1 + uuid_chars + 1 + 4 ≤ max_pod_name_len``
      (4 digits for the sequence number, 1 for the hyphen separator).
    """

    prefix: str = Field(
        "orb",
        description=(
            "Name prefix applied to every managed Kubernetes resource.  "
            "Must be a valid DNS-1123 label segment (lowercase alphanumeric "
            "and hyphens, starting and ending with alphanumeric, max 20 chars "
            "to leave room for the uuid segment)."
        ),
    )
    uuid_chars: int = Field(
        20,
        ge=8,
        le=32,
        description=(
            "Number of hex characters taken from the hyphen-stripped request "
            "UUID to form the name's uuid segment.  Default 20 (≈2^80 "
            "collision space — negligible at production scale; matches the "
            "historical pod-name pattern).  Must be at least 8 for a "
            "reasonable collision budget."
        ),
    )

    # Per-kind maximum name length budgets.  Callers use these to truncate
    # defensively; the model_validator enforces that prefix+uuid_chars fit.
    max_pod_name_len: int = Field(
        63,
        ge=20,
        le=253,
        description="Maximum DNS-1123 label length for Pod names (default 63).",
    )
    max_deployment_name_len: int = Field(
        47,
        ge=10,
        le=253,
        description=(
            "Maximum length for Deployment names.  Default 47 = 63 – 16-char "
            "ReplicaSet controller suffix budget."
        ),
    )
    max_statefulset_name_len: int = Field(
        57,
        ge=10,
        le=253,
        description=(
            "Maximum length for StatefulSet names.  Default 57 = 63 – 6-char "
            "ordinal suffix budget (up to -99999)."
        ),
    )
    max_job_name_len: int = Field(
        50,
        ge=10,
        le=253,
        description=(
            "Maximum length for Job names.  Default 50 = 63 – 13-char controller suffix margin."
        ),
    )

    @field_validator("prefix")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        """Reject non-DNS-1123 prefixes and enforce a max length of 20 chars."""
        if not v:
            raise ValueError("naming.prefix must be a non-empty string")
        if len(v) > 20:
            raise ValueError(
                f"naming.prefix {v!r} is too long ({len(v)} chars); max 20 to leave "
                "room for the uuid segment within the tightest kind budget."
            )
        if not _DNS_1123_LABEL_RE.match(v):
            raise ValueError(
                f"naming.prefix {v!r} is not a valid DNS-1123 label.  "
                "Must consist of lowercase alphanumeric characters and hyphens, "
                "start and end with an alphanumeric character."
            )
        return v

    @model_validator(mode="after")
    def _validate_budget(self) -> K8sNamingConfig:
        """Ensure prefix+uuid_chars fit within every per-kind length budget.

        Deployment is almost always the tightest constraint because its
        pod names have the longest controller-appended suffix.  The validator
        checks all four kinds so operators get a single, clear error message
        naming the offending kind rather than a confusing runtime truncation.
        """
        # <prefix>-<uuid_chars> for controller kinds
        controller_len = len(self.prefix) + 1 + self.uuid_chars
        # <prefix>-<uuid_chars>-<seq:04d> for pod (seq = 4 digits + 1 hyphen)
        pod_len = controller_len + 5

        failures: list[str] = []
        if pod_len > self.max_pod_name_len:
            failures.append(
                f"Pod: {pod_len} > max_pod_name_len={self.max_pod_name_len} "
                f"(prefix={self.prefix!r} + uuid_chars={self.uuid_chars} + seq-suffix=5)"
            )
        if controller_len > self.max_deployment_name_len:
            failures.append(
                f"Deployment: {controller_len} > max_deployment_name_len="
                f"{self.max_deployment_name_len} "
                f"(prefix={self.prefix!r} + uuid_chars={self.uuid_chars})"
            )
        if controller_len > self.max_statefulset_name_len:
            failures.append(
                f"StatefulSet: {controller_len} > max_statefulset_name_len="
                f"{self.max_statefulset_name_len} "
                f"(prefix={self.prefix!r} + uuid_chars={self.uuid_chars})"
            )
        if controller_len > self.max_job_name_len:
            failures.append(
                f"Job: {controller_len} > max_job_name_len={self.max_job_name_len} "
                f"(prefix={self.prefix!r} + uuid_chars={self.uuid_chars})"
            )
        if failures:
            raise ValueError(
                "K8sNamingConfig: prefix + uuid_chars overflow the per-kind name budget.  "
                "Reduce prefix length or uuid_chars:\n  " + "\n  ".join(failures)
            )
        return self


class K8sProviderConfig(BaseSettings, BaseProviderConfig):  # type: ignore[misc]
    """Top-level Kubernetes provider configuration.

    Field semantics:

    * ``kubeconfig_path`` / ``context`` — control out-of-cluster auth.  When
      both are unset the auth wrapper falls back to ``KUBECONFIG`` env var
      and the default kubeconfig location.
    * ``in_cluster`` — when ``None`` the provider auto-detects via the
      ``/var/run/secrets/kubernetes.io`` sentinel.  Explicit ``True`` /
      ``False`` short-circuits the detection.
    * ``namespace`` — single-namespace mode default.  Used when
      ``namespaces`` is ``None``.  When unset (``None``) the provider
      auto-detects the namespace from the in-cluster ServiceAccount token
      file (``/var/run/secrets/kubernetes.io/serviceaccount/namespace``)
      and falls back to ``"default"`` when not running in-cluster.
    * ``namespaces`` — multi-namespace mode.  ``None`` falls back to
      ``namespace``; an explicit list runs one watch task per entry;
      ``["*"]`` runs a cluster-scoped watch and requires cluster-level
      RBAC (see ``docs/providers/k8s/rbac.yaml``).
    * ``label_prefix`` — DNS-subdomain prefix used for the ``managed``,
      ``request-id``, ``machine-id`` and ``provider-api`` labels.
    * ``emit_legacy_labels`` — when ``True`` (default), in addition to the
      ``orb.io/*`` labels the provider emits the legacy
      ``symphony/open-resource-broker-reqid`` label so existing legacy
      watchers continue to function during the transition.
    * ``pod_timeout_seconds`` — bound on how long a pod may stay
      ``Pending`` before being treated as terminal.
    * ``stale_cache_timeout_seconds`` — once the in-process watch task is
      dead, the L1 cache is treated as stale after this many seconds and
      the provider falls back to on-demand list calls.
    * ``watch_enabled`` — global kill-switch for the asyncio watch task.
      Disabled by default for CLI mode, enabled by default for daemon mode
      (the provider strategy makes the runtime decision based on the
      ``WatchManager`` presence — this flag is the operator-level override).
    * ``min_kubernetes_version`` — minimum K8s API server version the
      provider supports.  Validated on health check.
    * ``auto_cleanup_orphans`` — when ``True`` the orphan GC deletes
      pods carrying the ``orb.io/managed=true`` label that have no
      matching record in ORB storage.  Default ``False`` so operators
      can debug pods themselves; orphans are logged either way.
    * ``orphan_gc_enabled`` — kill-switch for the periodic orphan-GC
      asyncio task.  Default ``False``; turn on once the operator is
      comfortable with the reconciler's behaviour in their environment.
    * ``orphan_gc_interval_seconds`` — how often the orphan GC task
      polls the cluster for managed pods.  Default 300 seconds (5 minutes).
    * ``orphan_min_age_seconds`` — orphan pods younger than this many
      seconds are skipped by the GC to avoid the in-flight request commit
      race.  Default 300 seconds (5 minutes).
    """

    model_config = SettingsConfigDict(  # type: ignore[assignment]
        env_prefix="ORB_K8S_",
        case_sensitive=False,
        populate_by_name=True,
        env_nested_delimiter="__",
        extra="forbid",
    )

    provider_type: str = "k8s"

    # Auth / cluster targeting
    kubeconfig_path: Optional[str] = Field(
        None, description="Path to a kubeconfig file (out-of-cluster auth)."
    )
    context: Optional[str] = Field(
        None, description="kubeconfig context name to select when loading."
    )
    in_cluster: Optional[bool] = Field(
        None,
        description=(
            "When ``None`` (default) the provider auto-detects in-cluster mode via "
            "the /var/run/secrets/kubernetes.io sentinel.  Explicit True/False "
            "short-circuits detection."
        ),
    )

    # HTTP proxy for apiserver connections
    proxy_url: Optional[str] = Field(
        None,
        description=(
            "HTTP/HTTPS proxy URL used for connections to the Kubernetes API "
            "server.  When set, this value is wired into "
            "``kubernetes.client.Configuration.proxy`` after the credentials are "
            "loaded and takes precedence over the ``HTTPS_PROXY`` / ``HTTP_PROXY`` "
            "environment variables.  When ``None`` (the default) the loaders fall "
            "back to the standard proxy environment variables.  Accepts an "
            "optional ``user:password@`` userinfo component; credentials are "
            "redacted from any DEBUG log output."
        ),
    )
    no_proxy: Optional[str] = Field(
        None,
        description=(
            "Comma-separated exclusion list wired into "
            "``kubernetes.client.Configuration.no_proxy``.  Hosts matching an "
            "entry bypass ``proxy_url``.  When set this takes precedence over the "
            "``NO_PROXY`` / ``no_proxy`` environment variables; when ``None`` the "
            "loaders fall back to those environment variables."
        ),
    )

    # Namespacing
    namespace: Optional[str] = Field(
        None,
        description=(
            "Single-namespace mode target namespace; used when ``namespaces`` is None.  "
            "When None (the default) the provider auto-detects the namespace from the "
            'in-cluster ServiceAccount token file and falls back to "default" when '
            "not running inside a pod."
        ),
    )
    namespaces: Optional[list[str]] = Field(
        None,
        description=(
            "Explicit list of namespaces to manage.  None = single-namespace mode "
            "(uses ``namespace``).  ['*'] = cluster-scoped watch (requires cluster RBAC)."
        ),
    )

    # Labels
    label_prefix: str = Field(
        "orb.io",
        description="DNS-subdomain prefix for ORB-emitted labels on managed resources.",
    )
    emit_legacy_labels: bool = Field(
        True,
        description=(
            "When True, also emit the legacy "
            "``symphony/open-resource-broker-reqid`` label alongside the "
            "modern ``orb.io/request-id`` label so legacy watchers continue "
            "to function during the transition."
        ),
    )

    # Pod defaults (applied at template-merge time by each handler)
    default_node_selector: Optional[dict[str, str]] = Field(
        None, description="Default ``nodeSelector`` applied to every managed pod."
    )
    default_tolerations: Optional[list[dict[str, Any]]] = Field(
        None,
        description=(
            "Default ``tolerations`` applied to every managed pod.  Values may "
            "be strings (key/operator/value/effect) or int (tolerationSeconds)."
        ),
    )
    default_image_pull_secret: Optional[str] = Field(
        None, description="Default image pull secret name applied to every managed pod."
    )
    default_restart_policy: Optional[str] = Field(
        None,
        description=(
            "Default ``restartPolicy`` for pods when a template does not set one. "
            "Per-kind constraints apply: Deployment/StatefulSet always use "
            "'Always'; Job accepts only 'Never'/'OnFailure'; bare Pod accepts any. "
            "None means each handler uses its built-in default (Pod/Job='Never')."
        ),
    )

    # Timing
    pod_timeout_seconds: int = Field(
        300,
        description="Maximum seconds a pod may stay Pending before being treated as terminal.",
    )
    delete_timed_out_pods: bool = Field(
        True,
        description=(
            "When True (default), pods that have been Pending past ``pod_timeout_seconds`` "
            "are deleted immediately after their status is rewritten to 'terminated'.  "
            "Set to False to revert to read-only behaviour — the status rewrite still "
            "happens but the pod is left on the cluster for operator inspection."
        ),
    )
    stale_cache_timeout_seconds: int = Field(
        600,
        description=(
            "Maximum seconds the L1 watch cache may serve reads after the watch task dies "
            "before the provider falls back to on-demand list calls."
        ),
    )

    # Watch
    watch_enabled: bool = Field(
        True,
        description=(
            "Operator-level override for the asyncio watch background task.  "
            "Set to False to force on-demand list behaviour even in daemon mode."
        ),
    )

    # Compatibility
    min_kubernetes_version: str = Field(
        "1.28",
        description="Minimum supported Kubernetes API server version (validated on health check).",
    )

    # Reconciliation / garbage collection
    auto_cleanup_orphans: bool = Field(
        False,
        description=(
            "When True the orphan garbage collector deletes managed pods that "
            "have no matching record in ORB storage.  Default False so operators "
            "can debug orphans; they are always logged regardless of this flag."
        ),
    )
    orphan_gc_enabled: bool = Field(
        False,
        description=(
            "Operator-level enable flag for the periodic orphan garbage-collection "
            "asyncio task.  Default False; flip to True once the operator is "
            "happy with the reconciler's behaviour in their environment."
        ),
    )
    orphan_gc_interval_seconds: int = Field(
        300,
        description=(
            "How often (in seconds) the orphan GC asyncio task polls the cluster "
            "for managed pods.  Default 300 (5 minutes)."
        ),
    )
    orphan_min_age_seconds: int = Field(
        300,
        description=(
            "Orphan pods younger than this many seconds are skipped by the GC to "
            "avoid races against in-flight request commits.  A pod created just "
            "before a GC sweep whose request record has not yet been persisted to "
            "storage would otherwise be deleted immediately.  Default 300 (5 minutes)."
        ),
    )

    # Pod-spec security audit
    audit_high_risk_pod_fields: bool = Field(
        True,
        description=(
            "When True (default), ORB inspects the rendered pod spec for high-risk fields "
            "(hostNetwork, hostPID, hostIPC, hostPath volumes, privileged containers, "
            "dangerous capabilities) at acquire time and logs each finding at WARNING level. "
            "Set to False to silence all audit warnings."
        ),
    )
    reject_high_risk_pod_fields: bool = Field(
        True,
        description=(
            "When True (default), ORB raises a K8sError instead of logging a warning when the "
            "rendered pod spec contains high-risk fields.  Requires "
            "``audit_high_risk_pod_fields=True`` (the default) to take effect.  "
            "Set to False to revert to warning-only behaviour (operators must opt out explicitly)."
        ),
    )

    # Node watching
    node_watch_enabled: bool = Field(
        False,
        description=(
            "Opt-in flag for the node-state watch background task.  When True, ORB "
            "starts a K8sNodeWatcher that streams ``CoreV1Api.list_node`` events and "
            "caches per-node metadata (instance type, zone, capacity type, CPU/memory "
            "capacity and allocatable values, and Ready condition).  The cached "
            "metadata is then surfaced in the ``provider_data`` block of each "
            "per-instance status dict returned by ``get_status``.  Default ``False`` "
            "because the node watcher requires a cluster-scoped RBAC grant "
            "(``ClusterRole`` with ``nodes: get/list/watch``) that is not needed for "
            "namespace-scoped pod management — see "
            "``docs/root/providers/k8s/rbac.yaml`` for the required rule."
        ),
    )

    # Events API watching (node-disruption visibility)
    events_watch_enabled: bool = Field(
        False,
        description=(
            "Opt-in flag for the k8s Events API watch background task.  When True, ORB "
            "starts a K8sEventsWatcher that streams ``CoreV1Api.list_event_for_all_namespaces`` "
            "filtered to ``involvedObject.kind=Node`` and caches Karpenter node-disruption "
            "events (e.g. 'Disrupting Node: Underutilized/Delete', 'Disrupting Node: "
            "Empty/Delete').  The cached disruption reason is available for surfacing in "
            "status responses.  Default ``False`` because the events watcher requires "
            "an additional RBAC grant (``events: get/list/watch`` on the core API group) "
            "that may not exist in every cluster -- see "
            "``docs/root/providers/k8s/rbac.yaml`` for the required rule."
        ),
    )

    # Periodic full-LIST backstop for the pod watcher
    periodic_resync_interval_seconds: int = Field(
        0,
        description=(
            "Interval in seconds at which the pod watcher performs a full LIST of all "
            "managed pods and reconciles the in-process cache, independent of 410-Gone "
            "responses.  Mirrors the legacy RefreshPodsTask (hfcron.py) which ran every "
            "~180 s as a correctness backstop against slow-drift apiservers.  "
            "Default 0 (disabled) to avoid extra apiserver load -- opt in by setting a "
            "positive value (e.g. 180).  When >0, a background asyncio task wakes every "
            "``periodic_resync_interval_seconds`` and calls the same _relist_snapshot "
            "path used on 410-Gone recovery, reconciling any cache drift that "
            "accumulated during a healthy watch session."
        ),
    )

    # Inbound HTTP auth
    inbound_auth_enabled: bool = Field(
        False,
        description=(
            "Opt-in flag for the Kubernetes inbound HTTP auth strategy "
            "(``KubeAuthStrategy``).  When True, ORB registers a "
            "``KubeAuthStrategy`` with the ``AuthRegistry`` so that callers "
            "of ORB's own REST API can be authenticated via a Kubernetes "
            "ServiceAccount Bearer token validated through the cluster's "
            "``authentication.k8s.io/v1 TokenReview`` API.  Default False "
            "because this requires a ``system:auth-delegator`` "
            "``ClusterRoleBinding`` (or a targeted ``tokenreviews: create`` "
            "RBAC grant) for the ORB pod's ServiceAccount — a privilege "
            "operators must opt in to deliberately."
        ),
    )

    # Controller-status cache
    controller_status_cache_ttl_seconds: float = Field(
        5.0,
        description=(
            "How long (in seconds) to serve a cached ``read_namespaced_*`` controller "
            "response before re-issuing the GET to the API server.  Applied to "
            "Deployment, StatefulSet and Job status polls.  At the default of 5 s, "
            "1 000 concurrent requests polling every 5 s produce at most ~200 "
            "controller GETs/s instead of the unthrottled ~200 GETs/s per workload.  "
            "Set to 0 (or any value <= 0) to disable the cache entirely — every poll "
            "will issue a direct GET.  Disabling prevents unbounded in-memory growth "
            "in environments where the handler is polled at very high frequency; the "
            "cache dict is not populated at all when TTL <= 0."
        ),
    )

    # Prometheus metrics
    metrics_enabled: bool = Field(
        True,
        description=(
            "Toggle for the Prometheus metrics surface.  When True the "
            "provider registers a :class:`K8sMetrics` instance against "
            "``prometheus_client.REGISTRY`` on start-up and records "
            "acquire/release counts, pod-creation outcomes, watch events, "
            "and watch reconnects from the handler and watcher hot paths. "
            "Set False to disable metric emission entirely (useful in test "
            "harnesses or when a downstream process owns the global "
            "registry)."
        ),
    )

    # Native spec escape hatch
    native_spec_enabled: bool = Field(
        False,
        description=(
            "Opt-in flag for the native-spec escape hatch.  When True, the "
            "per-handler create paths consult :attr:`K8sTemplate.native_spec` "
            "(or the provider's default Jinja template) and pass the rendered "
            "kubernetes API body straight to the SDK, bypassing the typed "
            "spec builders under ``providers.k8s.utilities``.  Default False "
            "so operators opt in deliberately — the escape hatch surrenders "
            "the typed-builder invariants (label injection, restart policy, "
            "selector wiring) to the operator's spec."
        ),
    )

    # Base directory for native-spec manifest files.  Mirrors the AWS
    # provider's ``spec_file_base_path`` field.  When set, relative
    # ``native_spec_path`` values on templates are resolved against this
    # directory; absolute paths are always used as-is.  When unset (the
    # default), relative paths are resolved against the process working
    # directory.  Path traversal outside this base is rejected at render
    # time with a clear error.
    native_spec_base_path: Optional[str] = Field(
        None,
        description=(
            "Base directory for native-spec manifest files (YAML or JSON).  "
            "Relative ``native_spec_path`` values on templates are resolved "
            "against this directory.  Absolute paths are always honoured.  "
            "When unset, relative paths are resolved against the current "
            "working directory.  Path traversal outside the base is rejected."
        ),
    )

    # Circuit-breaker and retry knobs.
    # These values are threaded through K8sHandlerRegistry.get_handler() into
    # each K8sHandlerBase constructor so operators can tune resilience behaviour
    # without recompiling.  Defaults match the K8sHandlerBase hardcoded values
    # so this change is a no-op for existing deployments.
    circuit_breaker_failure_threshold: int = Field(
        5,
        ge=1,  # 0 would trip the breaker on the very first call
        description=(
            "Number of consecutive apiserver failures that trips the per-handler "
            "circuit breaker.  Once open, calls fast-fail with "
            "``CircuitBreakerOpenError`` until the reset window expires.  "
            "Default 5 — matches the K8sHandlerBase hardcoded value."
        ),
    )
    circuit_breaker_reset_timeout: int = Field(
        60,
        ge=1,  # 0 seconds would immediately half-open, effectively disabling the breaker
        description=(
            "Seconds after the circuit opens before the breaker transitions to "
            "half-open and allows a probe request through.  Default 60 — matches "
            "the K8sHandlerBase hardcoded value."
        ),
    )
    max_retries: int = Field(
        3,
        ge=0,  # 0 = no retries (fail immediately); negative values are nonsensical
        description=(
            "Maximum number of retry attempts for transient apiserver errors "
            "(429 / 5xx) before giving up.  Non-recoverable status codes "
            "(400 / 401 / 403 / 404 / 409 / 410 / 422) are never retried regardless "
            "of this value.  Default 3 — matches the K8sHandlerBase hardcoded value."
        ),
    )
    retry_base_delay: float = Field(
        1.0,
        ge=0.01,  # near-zero delay would create a tight busy-loop on transient errors
        description=(
            "Base delay in seconds for the exponential-backoff retry strategy.  "
            "The first retry waits this many seconds; subsequent retries double "
            "the delay up to ``retry_max_delay``.  Default 1.0 — matches the "
            "K8sHandlerBase hardcoded value."
        ),
    )
    retry_max_delay: float = Field(
        30.0,
        ge=0.01,  # must be positive; near-zero is effectively no cap on busy-loop risk
        description=(
            "Maximum delay in seconds between retry attempts.  The exponential "
            "backoff is capped at this value.  Default 30.0 — matches the "
            "K8sHandlerBase hardcoded value."
        ),
    )

    # Resource naming policy
    naming: K8sNamingConfig = Field(
        default_factory=K8sNamingConfig,  # type: ignore[call-arg]
        description=(
            "Configurable resource-naming policy.  The generated name is "
            "``<prefix>-<uuid_segment>`` for controller kinds (Deployment, "
            "StatefulSet, Job) and ``<prefix>-<uuid_segment>-<seq:04d>`` for "
            "Pods.  ``uuid_segment`` is the first ``uuid_chars`` hex chars of "
            "the hyphen-stripped request UUID.  Defaults produce the same names "
            "as before this config was added, so existing resources are "
            "unaffected on upgrade."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_keys(cls, data: Any) -> Any:
        """Remap Symphony-era field names to their canonical equivalents.

        Mirrors
        :meth:`orb.providers.aws.configuration.config.AWSProviderConfig.normalize_connect_timeout`.
        Any key present in ``_LEGACY_FIELD_MAP`` is moved to the canonical
        name unless the canonical name is already set (to avoid clobbering
        an explicit operator value).
        """
        if not isinstance(data, dict):
            return data
        updated = dict(data)
        for legacy, canonical in _LEGACY_FIELD_MAP.items():
            if legacy in updated:
                # Operator-facing config-key deprecation (see docs deprecation.md
                # Pattern 2): this runs on a deserialised config dict, so the
                # signal must reach operators via logger.warning — a
                # DeprecationWarning is filtered in production and never surfaces.
                _get_logger().warning(
                    "K8s provider config key '%s' is deprecated; use '%s' instead.",
                    legacy,
                    canonical,
                )
                if canonical not in updated:
                    updated[canonical] = updated.pop(legacy)
                else:
                    # Canonical key wins; discard the legacy copy.
                    updated.pop(legacy)
        return updated

    @field_validator("default_restart_policy")
    @classmethod
    def _validate_default_restart_policy(cls, v: Optional[str]) -> Optional[str]:
        """Reject a ``default_restart_policy`` outside the Kubernetes-accepted set.

        Validated at config-construction time so an operator typo (e.g.
        ``'always'``) fails fast on load rather than as an opaque error at the
        first pod acquire.  Per-kind validity (Job rejecting ``Always``, etc.)
        is still enforced at spec-build time.
        """
        if v is not None and v not in ("Always", "OnFailure", "Never"):
            raise ValueError(
                f"default_restart_policy {v!r} is not a valid Kubernetes restartPolicy. "
                "Allowed values: 'Always', 'OnFailure', 'Never'."
            )
        return v

    @field_validator("namespace")
    @classmethod
    def _validate_namespace_format(cls, v: Optional[str]) -> Optional[str]:
        """Reject namespace values that do not satisfy RFC 1123 DNS subdomain rules.

        ``None`` passes through so the ``_resolve_namespace`` model_validator
        can set the in-cluster / default fall-back afterwards.
        """
        if v is None:
            return v
        if not _DNS_SUBDOMAIN_RE.match(v):
            raise ValueError(
                f"namespace {v!r} is not a valid RFC 1123 DNS subdomain.  "
                "Namespace names must consist of lowercase alphanumeric characters "
                "or hyphens, start and end with an alphanumeric character, "
                "and may be separated by dots (e.g. 'orb-system', 'production.jobs')."
            )
        return v

    @field_validator("context")
    @classmethod
    def _validate_context_format(cls, v: Optional[str]) -> Optional[str]:
        """Reject a context value that is set but empty or whitespace-only."""
        if v is not None and not v.strip():
            raise ValueError(
                "context must be a non-empty string when supplied; "
                "pass None to omit it (the provider will use the current kubeconfig context)."
            )
        return v

    @field_validator("proxy_url")
    @classmethod
    def _validate_proxy_url(cls, v: Optional[str]) -> Optional[str]:
        """Reject an empty/whitespace ``proxy_url`` and require an http(s) scheme.

        ``None`` passes through (proxy disabled / fall back to env vars).  A
        supplied value must be a non-empty ``http://`` or ``https://`` URL with
        a host component so an operator typo (e.g. a bare host without scheme)
        fails fast at config-construction time rather than being silently
        ignored by the SDK.
        """
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError(
                "proxy_url must be a non-empty URL when supplied; "
                "pass None to disable the explicit proxy and fall back to the "
                "HTTPS_PROXY / HTTP_PROXY environment variables."
            )
        parsed = urllib.parse.urlparse(stripped)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(
                f"proxy_url {v!r} is not a valid proxy URL.  Expected an "
                "'http://' or 'https://' URL with a host, e.g. "
                "'http://proxy.corp.example:3128'."
            )
        return stripped

    @field_validator("kubeconfig_path")
    @classmethod
    def _validate_kubeconfig_path(cls, v: Optional[str]) -> Optional[str]:
        """Verify the kubeconfig file exists when a path is explicitly supplied."""
        if v is None:
            return v
        path = Path(v)
        if not path.exists():
            raise ValueError(
                f"kubeconfig_path {v!r} does not exist.  "
                "Provide the absolute path to a readable kubeconfig file."
            )
        return v

    @field_validator("namespaces")
    @classmethod
    def _validate_namespaces(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        """Reject empty lists and bare empty strings inside ``namespaces``."""
        if v is None:
            return v
        if len(v) == 0:
            raise ValueError("namespaces must be None (single-namespace mode) or a non-empty list")
        if any(not isinstance(item, str) or not item.strip() for item in v):
            raise ValueError("namespaces entries must be non-empty strings")
        return v

    @model_validator(mode="after")
    def _resolve_namespace(self) -> K8sProviderConfig:
        """Resolve ``namespace`` to a concrete string when unset.

        Resolution order:

        1. Explicit operator value — used as-is.
        2. In-cluster ServiceAccount token file — used when ORB runs inside a
           Kubernetes pod and ``ORB_K8S_NAMESPACE`` is not set.
        3. ``"default"`` — final fall-back for out-of-cluster deployments.
        """
        if self.namespace is None:
            detected = _read_in_cluster_namespace()
            if detected is not None:
                _get_logger().info(
                    "K8s provider: namespace auto-detected from in-cluster ServiceAccount "
                    "token file (namespace=%r).",
                    detected,
                )
                self.namespace = detected
            else:
                self.namespace = "default"
        return self

    @model_validator(mode="after")
    def _validate_label_prefix(self) -> K8sProviderConfig:
        """``label_prefix`` must satisfy RFC 1123 DNS subdomain rules.

        Rejects empty strings, values containing ``=``, ``,``, ``(``, ``)``,
        spaces, slashes, or any character outside the RFC 1123 label character
        set.
        """
        prefix = self.label_prefix
        if not prefix:
            raise ValueError("label_prefix must be a non-empty string")
        if not _DNS_SUBDOMAIN_RE.match(prefix):
            raise ValueError(
                f"label_prefix {prefix!r} is not a valid RFC 1123 DNS subdomain.  "
                "The prefix must consist of lowercase alphanumeric characters or hyphens, "
                "start and end with an alphanumeric character, and may contain dots "
                "as label separators (e.g. 'orb.io', 'my-company.example.com').  "
                "Characters such as '=', ',', '(', ')', spaces, slashes, or uppercase "
                "letters are not permitted."
            )
        return self

    @model_validator(mode="after")
    def _validate_native_spec_requires_rejection(self) -> K8sProviderConfig:
        """``native_spec_enabled=True`` requires ``reject_high_risk_pod_fields=True``.

        Allowing native specs through the escape hatch whilst the high-risk
        pod-spec audit is configured to only warn (not reject) means an
        operator-supplied spec with privileged containers or hostPath volumes
        is silently accepted.  Requiring hard rejection when the escape hatch
        is open closes that gap.

        Operators who need to temporarily diagnose a failure can set
        ``native_spec_enabled=False`` or re-enable rejection explicitly.
        """
        if self.native_spec_enabled and not self.reject_high_risk_pod_fields:
            raise ValueError(
                "native_spec_enabled=True requires reject_high_risk_pod_fields=True.  "
                "Enabling the native-spec escape hatch whilst the high-risk pod-spec "
                "audit is configured to warn only is unsafe — set "
                "reject_high_risk_pod_fields=True (the default) or disable the "
                "native-spec hatch."
            )
        return self
