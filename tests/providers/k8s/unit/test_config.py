"""Unit tests for :class:`K8sProviderConfig`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from orb.providers.k8s.configuration.config import K8sProviderConfig


def test_defaults_match_documented_spec() -> None:
    """Default field values match the documented specification verbatim."""
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


def test_overrides_are_applied(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """All operator-supplied overrides flow through Pydantic."""
    # kubeconfig_path validator checks the file exists; create a real stub.
    kube_cfg = tmp_path / "kube.cfg"
    kube_cfg.write_text("apiVersion: v1\n", encoding="utf-8")
    cfg = K8sProviderConfig(
        kubeconfig_path=str(kube_cfg),
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

    assert cfg.kubeconfig_path == str(kube_cfg)
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


# ---------------------------------------------------------------------------
# Namespace auto-detection
# ---------------------------------------------------------------------------


def test_namespace_explicit_value_is_preserved() -> None:
    """An explicit ``namespace`` value is never overridden by auto-detection."""
    cfg = K8sProviderConfig(namespace="orb-system")
    assert cfg.namespace == "orb-system"


def test_namespace_auto_detected_from_in_cluster_file(tmp_path: pytest.TempPathFactory) -> None:
    """When the ServiceAccount namespace file exists, its content is used."""
    ns_file = tmp_path / "namespace"
    ns_file.write_text("kube-production\n", encoding="utf-8")

    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = "kube-production\n"

    with patch(
        "orb.providers.k8s.configuration.config._SA_NAMESPACE_FILE",
        mock_path,
    ):
        cfg = K8sProviderConfig()

    assert cfg.namespace == "kube-production"


def test_namespace_defaults_to_default_when_no_in_cluster_file() -> None:
    """When the ServiceAccount file is absent (out-of-cluster), namespace is ``"default"``."""
    mock_path = MagicMock()
    mock_path.exists.return_value = False

    with patch(
        "orb.providers.k8s.configuration.config._SA_NAMESPACE_FILE",
        mock_path,
    ):
        cfg = K8sProviderConfig()

    assert cfg.namespace == "default"


# ---------------------------------------------------------------------------
# Group B: native_spec + audit enforcement defaults and constraint
# ---------------------------------------------------------------------------


def test_reject_high_risk_pod_fields_default_is_true() -> None:
    """reject_high_risk_pod_fields must default to True (secure-by-default)."""
    cfg = K8sProviderConfig()
    assert cfg.reject_high_risk_pod_fields is True


def test_native_spec_enabled_requires_rejection() -> None:
    """native_spec_enabled=True with reject_high_risk_pod_fields=False is rejected."""
    with pytest.raises(ValidationError, match="native_spec_enabled=True requires"):
        K8sProviderConfig(native_spec_enabled=True, reject_high_risk_pod_fields=False)


def test_native_spec_enabled_with_rejection_is_valid() -> None:
    """native_spec_enabled=True is valid when reject_high_risk_pod_fields=True."""
    cfg = K8sProviderConfig(native_spec_enabled=True, reject_high_risk_pod_fields=True)
    assert cfg.native_spec_enabled is True
    assert cfg.reject_high_risk_pod_fields is True


def test_native_spec_disabled_with_no_rejection_is_valid() -> None:
    """native_spec_enabled=False with reject_high_risk_pod_fields=False is valid."""
    cfg = K8sProviderConfig(native_spec_enabled=False, reject_high_risk_pod_fields=False)
    assert cfg.native_spec_enabled is False
    assert cfg.reject_high_risk_pod_fields is False
