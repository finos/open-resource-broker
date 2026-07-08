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

from orb.providers.k8s.auth import (
    in_cluster as in_cluster_module,
    kubeconfig as kubeconfig_module,
)
from orb.providers.k8s.auth.in_cluster import is_in_cluster, load_in_cluster_config
from orb.providers.k8s.auth.kubeconfig import load_kubeconfig
from orb.providers.k8s.exceptions.k8s_errors import K8sAuthError


def test_is_in_cluster_true_when_sentinel_exists(tmp_path: Path) -> None:
    """Sentinel-path existence is the in-cluster signal."""
    assert is_in_cluster(sentinel=tmp_path) is True


def test_is_in_cluster_false_when_sentinel_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert is_in_cluster(sentinel=missing) is False


def test_load_in_cluster_config_wraps_sdk_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK exceptions are wrapped in ``K8sAuthError`` with context."""
    fake_config = SimpleNamespace(load_incluster_config=MagicMock(side_effect=RuntimeError("nope")))
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    with pytest.raises(K8sAuthError) as exc_info:
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

    with pytest.raises(K8sAuthError) as exc_info:
        load_kubeconfig(config_file="/nope", context=None)

    assert "/nope" in str(exc_info.value)


def test_modules_expose_documented_symbols() -> None:
    """Defensive: ensure the public auth API stays stable."""
    assert hasattr(in_cluster_module, "is_in_cluster")
    assert hasattr(in_cluster_module, "load_in_cluster_config")
    assert hasattr(kubeconfig_module, "load_kubeconfig")


# ---------------------------------------------------------------------------
# Group A: exec plugin allowlist and sanitised error messages
# ---------------------------------------------------------------------------


def test_load_kubeconfig_blocks_unknown_exec_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown exec plugins must be rejected with K8sAuthError."""
    kube_file = tmp_path / "config"
    kube_file.write_text(
        """
apiVersion: v1
kind: Config
users:
  - name: test-user
    user:
      exec:
        command: custom-auth-tool
        apiVersion: "client.authentication.k8s.io/v1beta1"
""",
        encoding="utf-8",
    )
    # Ensure the override env var is NOT set.
    monkeypatch.delenv("ORB_K8S_ALLOW_UNKNOWN_EXEC_PLUGIN", raising=False)

    with pytest.raises(K8sAuthError, match="not on the ORB allowlist"):
        from orb.providers.k8s.auth.kubeconfig import load_kubeconfig

        load_kubeconfig(config_file=str(kube_file))


def test_load_kubeconfig_allows_known_exec_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Well-known auth plugins must be permitted without raising."""
    kube_file = tmp_path / "config"
    kube_file.write_text(
        """
apiVersion: v1
kind: Config
users:
  - name: aws-user
    user:
      exec:
        command: aws
        apiVersion: "client.authentication.k8s.io/v1beta1"
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("ORB_K8S_ALLOW_UNKNOWN_EXEC_PLUGIN", raising=False)
    # Fake out the SDK so we don't need it installed.
    import sys
    from types import SimpleNamespace

    fake_config = SimpleNamespace(load_kube_config=MagicMock())
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    from orb.providers.k8s.auth.kubeconfig import load_kubeconfig

    load_kubeconfig(config_file=str(kube_file))
    fake_config.load_kube_config.assert_called_once()


def test_load_kubeconfig_allows_unknown_exec_plugin_with_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown exec plugins are allowed when ORB_K8S_ALLOW_UNKNOWN_EXEC_PLUGIN=1."""
    kube_file = tmp_path / "config"
    kube_file.write_text(
        """
apiVersion: v1
kind: Config
users:
  - name: test-user
    user:
      exec:
        command: custom-auth-tool
        apiVersion: "client.authentication.k8s.io/v1beta1"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ORB_K8S_ALLOW_UNKNOWN_EXEC_PLUGIN", "1")
    import sys
    from types import SimpleNamespace

    fake_config = SimpleNamespace(load_kube_config=MagicMock())
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    from orb.providers.k8s.auth.kubeconfig import load_kubeconfig

    load_kubeconfig(config_file=str(kube_file))
    fake_config.load_kube_config.assert_called_once()


def test_load_kubeconfig_sanitises_error_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SDK error text must not appear in the raised K8sAuthError message."""
    kube_file = tmp_path / "config"
    # No users block — no exec plugin to block.
    kube_file.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    import sys
    from types import SimpleNamespace

    secret_content = "SECRETTOKEN12345"
    fake_config = SimpleNamespace(
        load_kube_config=MagicMock(
            side_effect=RuntimeError(f"config parse error: {secret_content}")
        )
    )
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    from orb.providers.k8s.auth.kubeconfig import load_kubeconfig

    with pytest.raises(K8sAuthError) as exc_info:
        load_kubeconfig(config_file=str(kube_file))

    error_text = str(exc_info.value)
    # The sanitised message must NOT include raw SDK exception text.
    assert secret_content not in error_text
    # But it must include the config file path and a type name.
    assert str(kube_file) in error_text


def test_load_kubeconfig_allows_fullpath_exec_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full path to a known binary (e.g. /usr/local/bin/aws) must be allowed."""
    kube_file = tmp_path / "config"
    kube_file.write_text(
        """
apiVersion: v1
kind: Config
users:
  - name: aws-user
    user:
      exec:
        command: /usr/local/bin/aws
        apiVersion: "client.authentication.k8s.io/v1beta1"
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("ORB_K8S_ALLOW_UNKNOWN_EXEC_PLUGIN", raising=False)
    import sys
    from types import SimpleNamespace

    fake_config = SimpleNamespace(load_kube_config=MagicMock())
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    from orb.providers.k8s.auth.kubeconfig import load_kubeconfig

    load_kubeconfig(config_file=str(kube_file))
    fake_config.load_kube_config.assert_called_once()
