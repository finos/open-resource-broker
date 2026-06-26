"""Kubernetes provider configuration.

Single source of truth for the modern ``kubernetes`` provider.  Mirrors the
shape of :class:`orb.providers.aws.configuration.config.AWSProviderConfig`:
``BaseSettings`` with an ``ORB_K8S_`` env-var prefix plus a
``BaseProviderConfig`` mixin so the model integrates with the configuration
loader and the provider settings registry.

The fields below are the v1 surface defined in
``.claude/plans/PLAN-k8s-provider-implementation.md``.  Later phases add
template extension / DTO config classes alongside this file.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from orb.infrastructure.interfaces.provider import BaseProviderConfig


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
      ``namespaces`` is ``None``.
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
    """

    model_config = SettingsConfigDict(  # type: ignore[assignment]
        env_prefix="ORB_K8S_",
        case_sensitive=False,
        populate_by_name=True,
        env_nested_delimiter="__",
        extra="allow",
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

    # Namespacing
    namespace: str = Field(
        "default",
        description="Single-namespace mode target namespace; used when ``namespaces`` is None.",
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
    default_tolerations: Optional[list[dict[str, str]]] = Field(
        None, description="Default ``tolerations`` applied to every managed pod."
    )
    default_image_pull_secret: Optional[str] = Field(
        None, description="Default image pull secret name applied to every managed pod."
    )

    # Timing
    pod_timeout_seconds: int = Field(
        300,
        description="Maximum seconds a pod may stay Pending before being treated as terminal.",
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
    def _validate_label_prefix(self) -> "K8sProviderConfig":
        """``label_prefix`` must look like a DNS subdomain (RFC 1123)."""
        prefix = self.label_prefix
        if not prefix or "/" in prefix or " " in prefix:
            raise ValueError(
                "label_prefix must be a non-empty DNS subdomain (no slashes or spaces)"
            )
        return self
