"""Unit tests for :class:`K8sProviderConfig`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orb.providers.k8s.configuration.config import K8sProviderConfig


def test_defaults_match_phase_a_spec() -> None:
    """Default field values match the Phase A specification verbatim."""
    cfg = K8sProviderConfig()

    assert cfg.provider_type == "k8s"
    assert cfg.kubeconfig_path is None
    assert cfg.context is None
    assert cfg.in_cluster is None
    assert cfg.namespace == "default"
    assert cfg.namespaces is None
    assert cfg.label_prefix == "orb.io"
    assert cfg.emit_legacy_labels is True
    assert cfg.default_node_selector is None
    assert cfg.default_tolerations is None
    assert cfg.default_image_pull_secret is None
    assert cfg.pod_timeout_seconds == 300
    assert cfg.stale_cache_timeout_seconds == 600
    assert cfg.watch_enabled is True
    assert cfg.min_kubernetes_version == "1.28"


def test_overrides_are_applied() -> None:
    """All operator-supplied overrides flow through Pydantic."""
    cfg = K8sProviderConfig(
        kubeconfig_path="/tmp/kube.cfg",  # noqa: S108 — test fixture path
        context="prod",
        in_cluster=False,
        namespace="orb-system",
        namespaces=["orb-system", "orb-jobs"],
        label_prefix="example.com",
        emit_legacy_labels=False,
        default_node_selector={"role": "compute"},
        default_tolerations=[{"key": "dedicated", "operator": "Equal", "value": "orb"}],
        default_image_pull_secret="orb-registry",
        pod_timeout_seconds=600,
        stale_cache_timeout_seconds=900,
        watch_enabled=False,
        min_kubernetes_version="1.30",
    )

    assert cfg.kubeconfig_path == "/tmp/kube.cfg"  # noqa: S108
    assert cfg.context == "prod"
    assert cfg.in_cluster is False
    assert cfg.namespaces == ["orb-system", "orb-jobs"]
    assert cfg.label_prefix == "example.com"
    assert cfg.emit_legacy_labels is False
    assert cfg.default_node_selector == {"role": "compute"}
    assert cfg.pod_timeout_seconds == 600
    assert cfg.watch_enabled is False
    assert cfg.min_kubernetes_version == "1.30"


def test_namespaces_empty_list_rejected() -> None:
    """An empty list for ``namespaces`` is a misconfiguration."""
    with pytest.raises(ValidationError):
        K8sProviderConfig(namespaces=[])


def test_namespaces_blank_entry_rejected() -> None:
    """Whitespace-only namespace entries are rejected."""
    with pytest.raises(ValidationError):
        K8sProviderConfig(namespaces=["orb-system", "   "])


def test_label_prefix_rejects_slash() -> None:
    """``label_prefix`` must be a bare DNS subdomain (no path separators)."""
    with pytest.raises(ValidationError):
        K8sProviderConfig(label_prefix="orb.io/sub")


def test_label_prefix_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        K8sProviderConfig(label_prefix="")


def test_cluster_scoped_namespaces_marker_accepted() -> None:
    """``namespaces=['*']`` is the cluster-scoped watch sentinel."""
    cfg = K8sProviderConfig(namespaces=["*"])
    assert cfg.namespaces == ["*"]
