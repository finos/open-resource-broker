"""Unit tests for HTTP proxy support driven by :class:`K8sProviderConfig`.

These complement ``test_auth.py`` (which covers the env-var-only proxy path)
by exercising the config-supplied ``proxy_url`` / ``no_proxy`` fields and the
precedence rule that an explicit provider config value wins over ambient
environment variables.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.exceptions.k8s_exceptions import K8sAuthError

# ---------------------------------------------------------------------------
# Fake kubernetes module helpers
# ---------------------------------------------------------------------------


def _register_fake_kubernetes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    incluster: bool = False,
) -> MagicMock:
    """Register a fake kubernetes module and return the config spy instance.

    Returns the ``fake_configuration_instance`` that
    ``Configuration.get_default_copy`` will return so tests can assert
    ``proxy`` / ``no_proxy`` fields.
    """
    fake_cfg_instance = MagicMock()
    fake_cfg_instance.proxy = None
    fake_cfg_instance.no_proxy = None

    fake_configuration_cls = MagicMock()
    fake_configuration_cls.get_default_copy.return_value = fake_cfg_instance
    fake_configuration_cls.set_default = MagicMock()

    fake_client = SimpleNamespace(Configuration=fake_configuration_cls)
    if incluster:
        fake_config_mod = SimpleNamespace(load_incluster_config=MagicMock())
    else:
        fake_config_mod = SimpleNamespace(load_kube_config=MagicMock())
    fake_kubernetes = SimpleNamespace(config=fake_config_mod, client=fake_client)

    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config_mod)
    monkeypatch.setitem(sys.modules, "kubernetes.client", fake_client)

    return fake_cfg_instance


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# _resolve_proxy_url / _resolve_no_proxy — config precedence (kubeconfig)
# ---------------------------------------------------------------------------


def test_kubeconfig_resolve_proxy_url_config_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config proxy_url takes precedence over HTTPS_PROXY."""
    from orb.providers.k8s.auth.kubeconfig import _resolve_proxy_url

    monkeypatch.setenv("HTTPS_PROXY", "https://env-proxy:3128")
    assert _resolve_proxy_url("https://config-proxy:9000") == "https://config-proxy:9000"


def test_kubeconfig_resolve_proxy_url_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no config value is supplied, the env var is used."""
    from orb.providers.k8s.auth.kubeconfig import _resolve_proxy_url

    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "https://env-proxy:3128")
    assert _resolve_proxy_url(None) == "https://env-proxy:3128"


def test_kubeconfig_resolve_proxy_url_blank_config_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A whitespace-only config value must not shadow the env var."""
    from orb.providers.k8s.auth.kubeconfig import _resolve_proxy_url

    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://env-proxy:8080")
    assert _resolve_proxy_url("   ") == "http://env-proxy:8080"


def test_kubeconfig_resolve_no_proxy_config_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config no_proxy takes precedence over NO_PROXY."""
    from orb.providers.k8s.auth.kubeconfig import _resolve_no_proxy

    monkeypatch.setenv("NO_PROXY", "env.internal")
    assert _resolve_no_proxy("config.internal,.cluster.local") == "config.internal,.cluster.local"


# ---------------------------------------------------------------------------
# _apply_proxy_to_default_configuration — config precedence (kubeconfig)
# ---------------------------------------------------------------------------


def test_kubeconfig_apply_uses_config_proxy_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config proxy_url is written to Configuration.proxy over the env var."""
    monkeypatch.setenv("HTTPS_PROXY", "https://env-proxy:3128")
    fake_cfg = _register_fake_kubernetes(monkeypatch)

    from orb.providers.k8s.auth.kubeconfig import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(None, "https://config-proxy:9000", None)

    assert fake_cfg.proxy == "https://config-proxy:9000"


def test_kubeconfig_apply_uses_config_no_proxy_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config no_proxy is written to Configuration.no_proxy over the env var."""
    monkeypatch.setenv("NO_PROXY", "env.internal")
    fake_cfg = _register_fake_kubernetes(monkeypatch)

    from orb.providers.k8s.auth.kubeconfig import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(None, None, "config.internal")

    assert fake_cfg.no_proxy == "config.internal"


def test_kubeconfig_apply_config_proxy_when_no_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config proxy_url is applied even when no env vars are present."""
    _clear_proxy_env(monkeypatch)
    fake_cfg = _register_fake_kubernetes(monkeypatch)

    from orb.providers.k8s.auth.kubeconfig import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(None, "https://config-proxy:9000", None)

    assert fake_cfg.proxy == "https://config-proxy:9000"


# ---------------------------------------------------------------------------
# load_kubeconfig forwards config values
# ---------------------------------------------------------------------------


def test_load_kubeconfig_forwards_config_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_kubeconfig wires the supplied proxy_url/no_proxy into Configuration."""
    _clear_proxy_env(monkeypatch)
    kube_file = tmp_path / "config"
    kube_file.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")

    fake_cfg = _register_fake_kubernetes(monkeypatch)

    from orb.providers.k8s.auth.kubeconfig import load_kubeconfig

    load_kubeconfig(
        config_file=str(kube_file),
        proxy_url="https://config-proxy:9000",
        no_proxy="config.internal",
    )

    assert fake_cfg.proxy == "https://config-proxy:9000"
    assert fake_cfg.no_proxy == "config.internal"


# ---------------------------------------------------------------------------
# in-cluster loader config precedence
# ---------------------------------------------------------------------------


def test_incluster_resolve_proxy_url_config_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-cluster resolver: config proxy_url takes precedence over env."""
    from orb.providers.k8s.auth.in_cluster import _resolve_proxy_url

    monkeypatch.setenv("HTTPS_PROXY", "https://env-proxy:3128")
    assert _resolve_proxy_url("https://config-proxy:9000") == "https://config-proxy:9000"


def test_incluster_apply_uses_config_proxy_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-cluster apply: config value wins over the env var."""
    monkeypatch.setenv("HTTPS_PROXY", "https://env-proxy:3128")
    fake_cfg = _register_fake_kubernetes(monkeypatch, incluster=True)

    from orb.providers.k8s.auth.in_cluster import _apply_proxy_to_default_configuration

    _apply_proxy_to_default_configuration(None, "https://config-proxy:9000", "config.internal")

    assert fake_cfg.proxy == "https://config-proxy:9000"
    assert fake_cfg.no_proxy == "config.internal"


def test_load_in_cluster_config_forwards_config_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_in_cluster_config wires the supplied proxy_url/no_proxy."""
    _clear_proxy_env(monkeypatch)
    fake_cfg = _register_fake_kubernetes(monkeypatch, incluster=True)

    from orb.providers.k8s.auth.in_cluster import load_in_cluster_config

    load_in_cluster_config(proxy_url="https://config-proxy:9000", no_proxy="config.internal")

    assert fake_cfg.proxy == "https://config-proxy:9000"
    assert fake_cfg.no_proxy == "config.internal"


# ---------------------------------------------------------------------------
# InClusterAuthAdapter carries proxy config through load + refresh
# ---------------------------------------------------------------------------


def test_incluster_adapter_carries_proxy_on_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter.load() forwards its stored proxy_url/no_proxy to the loader."""
    from orb.providers.k8s.auth import in_cluster as in_cluster_mod
    from orb.providers.k8s.auth.in_cluster import InClusterAuthAdapter

    captured: dict[str, object] = {}

    def _fake_loader(logger=None, proxy_url=None, no_proxy=None):  # type: ignore[no-untyped-def]
        captured["proxy_url"] = proxy_url
        captured["no_proxy"] = no_proxy

    monkeypatch.setattr(in_cluster_mod, "load_in_cluster_config", _fake_loader)

    adapter = InClusterAuthAdapter(
        proxy_url="https://config-proxy:9000",
        no_proxy="config.internal",
    )
    adapter.load()

    assert captured == {"proxy_url": "https://config-proxy:9000", "no_proxy": "config.internal"}


def test_incluster_adapter_carries_proxy_on_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale refresh re-applies the stored proxy settings."""
    from orb.providers.k8s.auth import in_cluster as in_cluster_mod
    from orb.providers.k8s.auth.in_cluster import InClusterAuthAdapter

    calls: list[dict[str, object]] = []

    def _fake_loader(logger=None, proxy_url=None, no_proxy=None):  # type: ignore[no-untyped-def]
        calls.append({"proxy_url": proxy_url, "no_proxy": no_proxy})

    monkeypatch.setattr(in_cluster_mod, "load_in_cluster_config", _fake_loader)

    adapter = InClusterAuthAdapter(
        token_refresh_seconds=0,  # force staleness immediately
        proxy_url="https://config-proxy:9000",
        no_proxy="config.internal",
    )
    adapter.load()
    refreshed = adapter.refresh_if_stale()

    assert refreshed is True
    # Both the initial load and the refresh must carry the proxy config.
    assert len(calls) == 2
    assert all(
        c == {"proxy_url": "https://config-proxy:9000", "no_proxy": "config.internal"}
        for c in calls
    )


# ---------------------------------------------------------------------------
# K8sClient wires config proxy into the adapter and loaders
# ---------------------------------------------------------------------------


def test_k8s_client_adapter_receives_config_proxy() -> None:
    """K8sClient constructs its in-cluster adapter with the config proxy fields."""
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    cfg = K8sProviderConfig(  # type: ignore[call-arg]
        proxy_url="https://config-proxy:9000",
        no_proxy="config.internal",
    )
    client = K8sClient(config=cfg, logger=MagicMock())

    adapter = client._in_cluster_adapter
    assert adapter is not None
    assert adapter._proxy_url == "https://config-proxy:9000"
    assert adapter._no_proxy == "config.internal"


def test_k8s_client_load_config_forwards_proxy_to_kubeconfig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_config (kubeconfig branch) forwards config proxy fields."""
    from unittest.mock import patch

    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    kube_file = tmp_path / "kubeconfig"
    kube_file.write_text("# stub", encoding="utf-8")
    cfg = K8sProviderConfig(  # type: ignore[call-arg]
        in_cluster=False,
        kubeconfig_path=str(kube_file),
        proxy_url="https://config-proxy:9000",
        no_proxy="config.internal",
    )
    client = K8sClient(config=cfg, logger=MagicMock())

    with patch("orb.providers.k8s.infrastructure.k8s_client.load_kubeconfig") as mock_lkc:
        client.load_config()

    mock_lkc.assert_called_once_with(
        config_file=cfg.kubeconfig_path,
        context=cfg.context,
        logger=client._logger,
        proxy_url="https://config-proxy:9000",
        no_proxy="config.internal",
    )


# ---------------------------------------------------------------------------
# Config field validation
# ---------------------------------------------------------------------------


def test_config_proxy_url_defaults_to_none() -> None:
    cfg = K8sProviderConfig()  # type: ignore[call-arg]
    assert cfg.proxy_url is None
    assert cfg.no_proxy is None


def test_config_proxy_url_accepts_valid_url() -> None:
    cfg = K8sProviderConfig(proxy_url="http://proxy.corp.example:3128")  # type: ignore[call-arg]
    assert cfg.proxy_url == "http://proxy.corp.example:3128"


def test_config_proxy_url_strips_whitespace() -> None:
    cfg = K8sProviderConfig(proxy_url="  https://proxy:3128  ")  # type: ignore[call-arg]
    assert cfg.proxy_url == "https://proxy:3128"


def test_config_proxy_url_rejects_blank() -> None:
    with pytest.raises(ValueError, match="proxy_url must be a non-empty URL"):
        K8sProviderConfig(proxy_url="   ")  # type: ignore[call-arg]


def test_config_proxy_url_rejects_missing_scheme() -> None:
    with pytest.raises(ValueError, match="not a valid proxy URL"):
        K8sProviderConfig(proxy_url="proxy.corp.example:3128")  # type: ignore[call-arg]


def test_config_proxy_url_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="not a valid proxy URL"):
        K8sProviderConfig(proxy_url="socks5://proxy:1080")  # type: ignore[call-arg]


def test_config_proxy_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """ORB_K8S_PROXY_URL env var populates the field via BaseSettings."""
    monkeypatch.setenv("ORB_K8S_PROXY_URL", "https://env-config-proxy:3128")
    cfg = K8sProviderConfig()  # type: ignore[call-arg]
    assert cfg.proxy_url == "https://env-config-proxy:3128"


def test_load_in_cluster_config_wraps_errors_with_proxy_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SDK failure is still wrapped in K8sAuthError with proxy args supplied."""
    fake_config = SimpleNamespace(load_incluster_config=MagicMock(side_effect=RuntimeError("boom")))
    fake_kubernetes = SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)

    from orb.providers.k8s.auth.in_cluster import load_in_cluster_config

    with pytest.raises(K8sAuthError, match="boom"):
        load_in_cluster_config(proxy_url="https://config-proxy:9000")


# ---------------------------------------------------------------------------
# _redact_proxy_url — userinfo credentials must never be logged verbatim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "orb.providers.k8s.auth.in_cluster",
        "orb.providers.k8s.auth.kubeconfig",
    ],
)
def test_redact_proxy_url_redacts_scheme_and_credentials(module_name: str) -> None:
    """A standard ``scheme://user:pass@host`` value is redacted, host kept."""
    import importlib

    module = importlib.import_module(module_name)
    redacted = module._redact_proxy_url("https://user:s3cret@proxy.corp:3128")

    assert "s3cret" not in redacted
    assert "user" not in redacted
    assert "proxy.corp:3128" in redacted
    assert "***@proxy.corp:3128" in redacted


@pytest.mark.parametrize(
    "module_name",
    [
        "orb.providers.k8s.auth.in_cluster",
        "orb.providers.k8s.auth.kubeconfig",
    ],
)
def test_redact_proxy_url_redacts_schemeless_credentials(module_name: str) -> None:
    """A scheme-less ``user:pass@host`` value must still be redacted.

    ``urllib.parse.urlparse`` leaves ``username``/``password`` unset when no
    scheme is present, so the naive check would log the raw credentials.  The
    fallback userinfo detection must catch this case.
    """
    import importlib

    module = importlib.import_module(module_name)
    redacted = module._redact_proxy_url("user:s3cret@proxy.corp:3128")

    assert "s3cret" not in redacted
    assert "***@proxy.corp:3128" in redacted


@pytest.mark.parametrize(
    "module_name",
    [
        "orb.providers.k8s.auth.in_cluster",
        "orb.providers.k8s.auth.kubeconfig",
    ],
)
def test_redact_proxy_url_leaves_credentialless_url_untouched(module_name: str) -> None:
    """A URL without userinfo is returned unchanged (nothing to redact)."""
    import importlib

    module = importlib.import_module(module_name)
    assert module._redact_proxy_url("https://proxy.corp:3128") == "https://proxy.corp:3128"
    assert module._redact_proxy_url("proxy.corp:3128") == "proxy.corp:3128"


@pytest.mark.parametrize(
    "module_name",
    [
        "orb.providers.k8s.auth.in_cluster",
        "orb.providers.k8s.auth.kubeconfig",
    ],
)
def test_apply_proxy_debug_log_redacts_schemeless_credentials(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    """The DEBUG log emitted on proxy wiring must not contain credentials.

    Exercises the full ``_apply_proxy_to_default_configuration`` path with a
    scheme-less credentialed proxy so the assertion covers what is actually
    written to the log, not just the helper in isolation.
    """
    import importlib

    _clear_proxy_env(monkeypatch)
    incluster = module_name.endswith("in_cluster")
    _register_fake_kubernetes(monkeypatch, incluster=incluster)

    module = importlib.import_module(module_name)
    mock_logger = MagicMock()

    module._apply_proxy_to_default_configuration(mock_logger, "user:s3cret@proxy.corp:3128", None)

    logged = " ".join(str(c) for c in mock_logger.debug.call_args_list)
    assert "s3cret" not in logged
    assert "***@proxy.corp:3128" in logged
