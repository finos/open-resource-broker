"""Unit tests for the Kubernetes auth wrappers.

The wrappers are thin glue around ``kubernetes.config.load_*`` — these tests
only verify the error-handling and detection logic, not the upstream
kubernetes SDK behaviour.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.providers.kubernetes.auth import (
    in_cluster as in_cluster_module,
    kubeconfig as kubeconfig_module,
)
from orb.providers.kubernetes.auth.in_cluster import is_in_cluster, load_in_cluster_config
from orb.providers.kubernetes.auth.kubeconfig import load_kubeconfig
from orb.providers.kubernetes.exceptions.k8s_errors import KubernetesAuthError


def test_is_in_cluster_true_when_sentinel_exists(tmp_path: Path) -> None:
    """Sentinel-path existence is the in-cluster signal."""
    assert is_in_cluster(sentinel=tmp_path) is True


def test_is_in_cluster_false_when_sentinel_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert is_in_cluster(sentinel=missing) is False


def test_load_in_cluster_config_wraps_sdk_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK exceptions are wrapped in ``KubernetesAuthError`` with context."""
    fake_config = SimpleNamespace(load_incluster_config=MagicMock(side_effect=RuntimeError("nope")))
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    with pytest.raises(KubernetesAuthError) as exc_info:
        load_in_cluster_config()

    assert "nope" in str(exc_info.value)
    fake_config.load_incluster_config.assert_called_once()


def test_load_in_cluster_config_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_config = SimpleNamespace(load_incluster_config=MagicMock())
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    load_in_cluster_config()

    fake_config.load_incluster_config.assert_called_once()


def test_load_kubeconfig_passes_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """``config_file`` and ``context`` are forwarded verbatim to the SDK."""
    fake_config = SimpleNamespace(load_kube_config=MagicMock())
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    load_kubeconfig(config_file="/etc/kube.cfg", context="prod")

    fake_config.load_kube_config.assert_called_once_with(
        config_file="/etc/kube.cfg", context="prod"
    )


def test_load_kubeconfig_wraps_sdk_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_config = SimpleNamespace(load_kube_config=MagicMock(side_effect=FileNotFoundError("x")))
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    with pytest.raises(KubernetesAuthError) as exc_info:
        load_kubeconfig(config_file="/nope", context=None)

    assert "/nope" in str(exc_info.value)


def test_modules_expose_documented_symbols() -> None:
    """Defensive: ensure the public auth API stays stable."""
    assert hasattr(in_cluster_module, "is_in_cluster")
    assert hasattr(in_cluster_module, "load_in_cluster_config")
    assert hasattr(kubeconfig_module, "load_kubeconfig")
