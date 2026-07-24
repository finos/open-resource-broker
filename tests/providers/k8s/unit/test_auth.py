"""Unit tests for the Kubernetes auth wrappers.

The wrappers are thin glue around ``kubernetes.config.load_*`` — these tests
only verify the error-handling and detection logic, not the upstream
kubernetes SDK behaviour.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.k8s.auth import (
    in_cluster as in_cluster_module,
    kubeconfig as kubeconfig_module,
)
from orb.providers.k8s.auth.in_cluster import is_in_cluster, load_in_cluster_config
from orb.providers.k8s.auth.kubeconfig import load_kubeconfig
from orb.providers.k8s.exceptions.k8s_exceptions import K8sAuthError


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


def test_load_kubeconfig_forwards_client_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """A supplied client_configuration is threaded to the SDK so a LIVE config is reloaded.

    This is the 401-recovery seam: refreshed exec-token credentials must land on
    the in-flight ApiClient's own Configuration, not only the global default.
    """
    fake_config = SimpleNamespace(load_kube_config=MagicMock())
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    sentinel_config = object()
    load_kubeconfig(
        config_file="/etc/kube.cfg", context="prod", client_configuration=sentinel_config
    )

    fake_config.load_kube_config.assert_called_once_with(
        config_file="/etc/kube.cfg", context="prod", client_configuration=sentinel_config
    )


def test_load_kubeconfig_forces_exec_plugin_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TTY-attached load must drive the exec plugin non-interactively.

    The kubernetes SDK's ``ExecProvider`` decides interactivity solely from
    ``sys.stdout.isatty()``; on a real terminal it runs the credential plugin
    interactively, which fails to attach the minted bearer token for
    ``credential_process``-backed AWS profiles (empty Authorization header ->
    401).  ORB's loader must neutralise that: even with ``isatty()`` forced
    ``True``, the SDK's ``load_kube_config`` must observe ``isatty()`` ==
    ``False`` at call time.
    """
    observed_isatty: list[bool] = []

    def _fake_load_kube_config(**_kwargs: object) -> None:
        # Capture what the SDK's ExecProvider would read at the exact moment
        # ORB delegates to it.
        observed_isatty.append(sys.stdout.isatty())

    fake_config = SimpleNamespace(load_kube_config=MagicMock(side_effect=_fake_load_kube_config))
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    # Force the failing precondition: pretend stdout is a real terminal.
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    assert sys.stdout.isatty() is True

    load_kubeconfig(config_file="/etc/kube.cfg", context="prod")

    assert observed_isatty == [False], (
        "load_kube_config must run inside the non-interactive guard so the exec "
        "plugin takes its non-interactive branch"
    )
    # The guard must be scoped: stdout is restored to a TTY afterwards.
    assert sys.stdout.isatty() is True


def test_force_non_interactive_exec_restores_stdout_on_error() -> None:
    """The guard restores ``sys.stdout`` even when the wrapped block raises."""
    original = sys.stdout
    raised = False
    try:
        with kubeconfig_module._force_non_interactive_exec():
            assert sys.stdout.isatty() is False
            raise RuntimeError("boom")
    except RuntimeError:
        raised = True
    assert raised
    assert sys.stdout is original


def test_request_path_token_refresh_hook_forces_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wrapped refresh hook re-mints non-interactively on the live request path.

    This is the steady-state 401 exposure: the SDK fires
    ``Configuration.refresh_api_key_hook`` lazily while building the
    Authorization header of an ordinary request (resource verb / watch open /
    status poll).  When the embedded token expiry lapses that hook re-runs the
    exec credential plugin, whose interactivity is keyed off
    ``sys.stdout.isatty()``.  On a TTY the unwrapped hook would re-mint
    interactively and drop the token; the wrap installed by ORB must force the
    non-interactive branch and then restore stdout.
    """
    observed_isatty: list[bool] = []

    def _sdk_refresh(_cfg: object) -> None:
        # Mirrors ExecProvider.run's isatty() decision at re-mint time.
        observed_isatty.append(sys.stdout.isatty())

    config = SimpleNamespace(refresh_api_key_hook=_sdk_refresh)

    kubeconfig_module._install_non_interactive_refresh_hook(config)

    # Force a real terminal, then fire the wrapped hook as the SDK would.
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    assert sys.stdout.isatty() is True

    config.refresh_api_key_hook(config)

    assert observed_isatty == [False], (
        "the request-path refresh hook must re-mint the exec token "
        "non-interactively even when ORB is attached to a TTY"
    )
    # Scoped: stdout is restored to the TTY once the mint completes.
    assert sys.stdout.isatty() is True


def test_install_non_interactive_refresh_hook_is_idempotent() -> None:
    """Re-installing the wrap on an already-wrapped hook is a no-op.

    ``load_kubeconfig`` / ``force_token_refresh`` may re-run repeatedly; the
    wrap must not stack (which would nest guards without benefit).
    """
    calls: list[int] = []

    def _hook(_cfg: object) -> None:
        calls.append(1)

    config = SimpleNamespace(refresh_api_key_hook=_hook)

    kubeconfig_module._install_non_interactive_refresh_hook(config)
    wrapped_once = config.refresh_api_key_hook
    assert wrapped_once is not _hook

    kubeconfig_module._install_non_interactive_refresh_hook(config)
    assert config.refresh_api_key_hook is wrapped_once


def test_install_non_interactive_refresh_hook_noop_without_hook() -> None:
    """Non-exec auth (no refresh hook) leaves the configuration untouched.

    Static bearer-token / client-certificate configs never set a
    ``refresh_api_key_hook`` and never consult ``isatty()``; the installer must
    be inert for them.
    """
    config = SimpleNamespace(refresh_api_key_hook=None)
    kubeconfig_module._install_non_interactive_refresh_hook(config)
    assert config.refresh_api_key_hook is None


def test_load_kubeconfig_wraps_refresh_hook_on_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a global-default load, the SDK's refresh hook is wrapped in place.

    The initial ``load_kube_config`` writes the fresh config (with its exec
    refresh hook) into ``Configuration._default``.  ORB must wrap that hook so
    ``ApiClient`` instances built afterwards — which deep-copy the default —
    inherit the guarded callable and re-mint non-interactively on every request.
    """
    observed_isatty: list[bool] = []

    def _sdk_refresh(_cfg: object) -> None:
        observed_isatty.append(sys.stdout.isatty())

    default_config = SimpleNamespace(refresh_api_key_hook=_sdk_refresh)
    fake_configuration_cls = SimpleNamespace(_default=default_config)
    fake_client_mod = SimpleNamespace(Configuration=fake_configuration_cls)
    fake_config = SimpleNamespace(load_kube_config=MagicMock())
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes.client", fake_client_mod)

    load_kubeconfig(config_file="/etc/kube.cfg", context="prod")

    # The default's hook was replaced by the wrapped version.
    assert default_config.refresh_api_key_hook is not _sdk_refresh

    # Firing the wrapped hook on a TTY re-mints non-interactively.
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    default_config.refresh_api_key_hook(default_config)
    assert observed_isatty == [False]


def test_load_in_cluster_config_forwards_client_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A supplied client_configuration is threaded to load_incluster_config."""
    fake_config = SimpleNamespace(load_incluster_config=MagicMock())
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    sentinel_config = object()
    load_in_cluster_config(client_configuration=sentinel_config)

    fake_config.load_incluster_config.assert_called_once_with(client_configuration=sentinel_config)


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


# ---------------------------------------------------------------------------
# Group B: HTTP proxy wiring — kubeconfig loader
# ---------------------------------------------------------------------------


def _make_fake_kubernetes_with_configuration(
    monkeypatch: pytest.MonkeyPatch,
    kube_file: Path,
) -> MagicMock:
    """Register a fake kubernetes module and return a spy on Configuration.

    Returns the ``fake_configuration_instance`` that ``Configuration.get_default_copy``
    will return so tests can assert ``proxy``/``no_proxy`` fields.
    """
    from types import SimpleNamespace

    # Fresh mutable object used as the "current global configuration"
    fake_cfg_instance = MagicMock()
    fake_cfg_instance.proxy = None
    fake_cfg_instance.no_proxy = None

    fake_configuration_cls = MagicMock()
    fake_configuration_cls.get_default_copy.return_value = fake_cfg_instance
    fake_configuration_cls.set_default = MagicMock()

    fake_client = SimpleNamespace(Configuration=fake_configuration_cls)
    fake_config_mod = SimpleNamespace(load_kube_config=MagicMock())
    fake_kubernetes = SimpleNamespace(config=fake_config_mod, client=fake_client)

    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config_mod)
    monkeypatch.setitem(sys.modules, "kubernetes.client", fake_client)

    return fake_cfg_instance


def test_load_kubeconfig_sets_proxy_from_https_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTPS_PROXY must be wired into Configuration.proxy after kubeconfig load."""
    kube_file = tmp_path / "config"
    kube_file.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.corp.example:3128")
    for var in ("https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    fake_cfg = _make_fake_kubernetes_with_configuration(monkeypatch, kube_file)

    from orb.providers.k8s.auth.kubeconfig import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(None)

    assert fake_cfg.proxy == "https://proxy.corp.example:3128"


def test_load_kubeconfig_sets_proxy_from_http_proxy_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP_PROXY must be used when no HTTPS_PROXY variant is set."""
    kube_file = tmp_path / "config"
    kube_file.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp.example:8080")
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    fake_cfg = _make_fake_kubernetes_with_configuration(monkeypatch, kube_file)

    from orb.providers.k8s.auth.kubeconfig import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(None)

    assert fake_cfg.proxy == "http://proxy.corp.example:8080"


def test_load_kubeconfig_prefers_https_proxy_over_http_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTPS_PROXY must take precedence over HTTP_PROXY."""
    kube_file = tmp_path / "config"
    kube_file.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "https://secure-proxy:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://plain-proxy:8080")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    fake_cfg = _make_fake_kubernetes_with_configuration(monkeypatch, kube_file)

    from orb.providers.k8s.auth.kubeconfig import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(None)

    assert fake_cfg.proxy == "https://secure-proxy:3128"


def test_load_kubeconfig_no_proxy_is_noop_when_no_env_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no proxy env vars are set, Configuration must not be touched."""
    kube_file = tmp_path / "config"
    kube_file.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)

    _make_fake_kubernetes_with_configuration(monkeypatch, kube_file)

    from kubernetes.client import Configuration as _RealConfiguration

    with patch.object(_RealConfiguration, "set_default") as mock_set_default:
        from orb.providers.k8s.auth.kubeconfig import _apply_proxy_to_default_configuration

        _apply_proxy_to_default_configuration(None)
        mock_set_default.assert_not_called()


def test_load_kubeconfig_wires_no_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO_PROXY must be wired into Configuration.no_proxy."""
    kube_file = tmp_path / "config"
    kube_file.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.corp:3128")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,.internal")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    fake_cfg = _make_fake_kubernetes_with_configuration(monkeypatch, kube_file)

    from orb.providers.k8s.auth.kubeconfig import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(None)

    assert fake_cfg.no_proxy == "localhost,127.0.0.1,.internal"


def test_load_kubeconfig_logs_proxy_at_debug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a logger is provided and proxy is set, a debug message must be emitted."""
    kube_file = tmp_path / "config"
    kube_file.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.corp:3128")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    _make_fake_kubernetes_with_configuration(monkeypatch, kube_file)
    mock_logger = MagicMock()

    from orb.providers.k8s.auth.kubeconfig import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(mock_logger)

    mock_logger.debug.assert_called()
    debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
    assert any("proxy" in c.lower() for c in debug_calls)


# ---------------------------------------------------------------------------
# Group C: HTTP proxy wiring — in-cluster loader
# ---------------------------------------------------------------------------


def _make_fake_kubernetes_for_in_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> MagicMock:
    """Register a fake kubernetes module for in-cluster tests.

    Returns the ``fake_configuration_instance`` that ``Configuration.get_default_copy``
    will return.
    """
    from types import SimpleNamespace

    fake_cfg_instance = MagicMock()
    fake_cfg_instance.proxy = None
    fake_cfg_instance.no_proxy = None

    fake_configuration_cls = MagicMock()
    fake_configuration_cls.get_default_copy.return_value = fake_cfg_instance
    fake_configuration_cls.set_default = MagicMock()

    fake_client = SimpleNamespace(Configuration=fake_configuration_cls)
    fake_config_mod = SimpleNamespace(load_incluster_config=MagicMock())
    fake_kubernetes = SimpleNamespace(config=fake_config_mod, client=fake_client)

    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config_mod)
    monkeypatch.setitem(sys.modules, "kubernetes.client", fake_client)

    return fake_cfg_instance


def test_load_in_cluster_config_sets_proxy_from_https_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTPS_PROXY must be wired into Configuration.proxy after in-cluster load."""
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.corp.example:3128")
    for var in ("https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    fake_cfg = _make_fake_kubernetes_for_in_cluster(monkeypatch)

    from orb.providers.k8s.auth.in_cluster import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(None)

    assert fake_cfg.proxy == "https://proxy.corp.example:3128"


def test_load_in_cluster_config_sets_proxy_from_http_proxy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP_PROXY must be used when no HTTPS_PROXY variant is set."""
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp.example:8080")
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    fake_cfg = _make_fake_kubernetes_for_in_cluster(monkeypatch)

    from orb.providers.k8s.auth.in_cluster import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(None)

    assert fake_cfg.proxy == "http://proxy.corp.example:8080"


def test_load_in_cluster_config_no_proxy_is_noop_when_no_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no proxy env vars are set, Configuration must not be touched."""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)

    from kubernetes.client import Configuration as _RealConfiguration

    with patch.object(_RealConfiguration, "set_default") as mock_set_default:
        from orb.providers.k8s.auth.in_cluster import _apply_proxy_to_default_configuration

        _apply_proxy_to_default_configuration(None)
        mock_set_default.assert_not_called()


def test_load_in_cluster_config_wires_no_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO_PROXY must be wired into Configuration.no_proxy for in-cluster mode."""
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.corp:3128")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,.cluster.local")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    fake_cfg = _make_fake_kubernetes_for_in_cluster(monkeypatch)

    from orb.providers.k8s.auth.in_cluster import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(None)

    assert fake_cfg.no_proxy == "localhost,127.0.0.1,.cluster.local"


def test_load_in_cluster_config_logs_proxy_at_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a logger is provided and proxy is set, a debug message must be emitted."""
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.corp:3128")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    _make_fake_kubernetes_for_in_cluster(monkeypatch)
    mock_logger = MagicMock()

    from orb.providers.k8s.auth.in_cluster import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(mock_logger)

    mock_logger.debug.assert_called()
    debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
    assert any("proxy" in c.lower() for c in debug_calls)


# ---------------------------------------------------------------------------
# Regression: proxy URL credential redaction (Fix 1)
# ---------------------------------------------------------------------------


def test_redact_proxy_url_strips_credentials() -> None:
    """_redact_proxy_url must replace user:pass@ with ***@ in the log string."""
    from orb.providers.k8s.auth.in_cluster import _redact_proxy_url

    raw = "https://user:secret@proxy.corp.example:3128"
    redacted = _redact_proxy_url(raw)
    assert "secret" not in redacted, "password must not appear in redacted URL"
    assert "user" not in redacted, "username must not appear in redacted URL"
    assert "proxy.corp.example" in redacted, "host must remain visible for diagnostics"
    assert "3128" in redacted, "port must remain visible for diagnostics"
    assert "***" in redacted, "redacted marker must be present"


def test_redact_proxy_url_no_credentials_passes_through() -> None:
    """A proxy URL without credentials must be returned unchanged."""
    from orb.providers.k8s.auth.in_cluster import _redact_proxy_url

    plain = "https://proxy.corp.example:3128"
    assert _redact_proxy_url(plain) == plain


def test_redact_proxy_url_kubeconfig_strips_credentials() -> None:
    """kubeconfig._redact_proxy_url also redacts credentials."""
    from orb.providers.k8s.auth.kubeconfig import _redact_proxy_url as kc_redact

    raw = "http://admin:hunter2@proxy.internal:8080"
    redacted = kc_redact(raw)
    assert "hunter2" not in redacted
    assert "admin" not in redacted
    assert "proxy.internal" in redacted


def test_in_cluster_debug_log_does_not_log_raw_proxy_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DEBUG log emitted for proxy wiring must not contain the raw password."""
    monkeypatch.setenv("HTTPS_PROXY", "https://user:supersecret@proxy.corp:3128")
    for var in ("https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)

    _make_fake_kubernetes_for_in_cluster(monkeypatch)
    mock_logger = MagicMock()

    from orb.providers.k8s.auth.in_cluster import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(mock_logger)

    # Collect all debug call args as strings
    all_debug_text = " ".join(
        str(arg) for call in mock_logger.debug.call_args_list for arg in call.args
    )
    assert "supersecret" not in all_debug_text, "Raw proxy password must not appear in debug log"
    assert "proxy.corp" in all_debug_text, "Host should still be logged for diagnostics"


def test_kubeconfig_debug_log_does_not_log_raw_proxy_creds(
    tmp_path: "Path",
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kubeconfig debug log must not emit the raw proxy password."""
    monkeypatch.setenv("HTTPS_PROXY", "https://alice:topsecret@proxy.corp:3128")
    for var in ("https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)

    kube_file = tmp_path / "config"
    kube_file.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    _make_fake_kubernetes_with_configuration(monkeypatch, kube_file)
    mock_logger = MagicMock()

    from orb.providers.k8s.auth.kubeconfig import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(mock_logger)

    all_debug_text = " ".join(
        str(arg) for call in mock_logger.debug.call_args_list for arg in call.args
    )
    assert "topsecret" not in all_debug_text, (
        "Raw proxy password must not appear in kubeconfig debug log"
    )
    assert "proxy.corp" in all_debug_text
