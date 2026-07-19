"""Unit tests for :class:`K8sClient` — token refresh and cleanup behaviour.

Backfill coverage added in Group T1:
* load_config() with in_cluster=False + a valid kubeconfig path
* load_config() with in_cluster=False + an invalid kubeconfig path (raises K8sAuthError)
* api_client lazy-wiring (property builds an ApiClient on first access)
* core_v1, apps_v1, batch_v1 lazy accessors
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig

if TYPE_CHECKING:
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient


def _make_client(api_client: object | None = None) -> K8sClient:
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    mock_logger = MagicMock()
    cfg = K8sProviderConfig()
    return K8sClient(config=cfg, logger=mock_logger, api_client=api_client)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# load_config() — kubeconfig path
# ---------------------------------------------------------------------------


def test_load_config_with_valid_kubeconfig(tmp_path: pytest.TempPathFactory) -> None:
    """load_config with in_cluster=False calls load_kubeconfig without raising."""
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    # K8sProviderConfig validates that kubeconfig_path exists; create a stub file.
    kube_file = tmp_path / "kubeconfig"  # type: ignore[operator]
    kube_file.write_text("# stub kubeconfig for test")

    cfg = K8sProviderConfig(in_cluster=False, kubeconfig_path=str(kube_file))  # type: ignore[call-arg]
    client = K8sClient(config=cfg, logger=MagicMock())

    with patch("orb.providers.k8s.infrastructure.k8s_client.load_kubeconfig") as mock_lkc:
        client.load_config()

    mock_lkc.assert_called_once_with(
        config_file=cfg.kubeconfig_path,
        context=cfg.context,
        logger=client._logger,
        proxy_url=cfg.proxy_url,
        no_proxy=cfg.no_proxy,
    )


def test_load_config_propagates_k8s_auth_error(tmp_path: pytest.TempPathFactory) -> None:
    """load_config with in_cluster=False propagates K8sAuthError from load_kubeconfig."""
    from orb.providers.k8s.exceptions.k8s_exceptions import K8sAuthError
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    cfg = K8sProviderConfig(in_cluster=False)  # type: ignore[call-arg]
    client = K8sClient(config=cfg, logger=MagicMock())

    with patch(
        "orb.providers.k8s.infrastructure.k8s_client.load_kubeconfig",
        side_effect=K8sAuthError("bad kubeconfig"),
    ):
        with pytest.raises(K8sAuthError, match="bad kubeconfig"):
            client.load_config()


def test_load_config_skips_when_api_client_already_set() -> None:
    """load_config is a no-op when a pre-built api_client has been injected."""
    mock_api_client = MagicMock()
    client = _make_client(api_client=mock_api_client)

    with patch("orb.providers.k8s.infrastructure.k8s_client.load_kubeconfig") as mock_lkc:
        with patch(
            "orb.providers.k8s.infrastructure.k8s_client.load_in_cluster_config"
        ) as mock_lic:
            client.load_config()

    mock_lkc.assert_not_called()
    mock_lic.assert_not_called()


def test_load_config_in_cluster_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_config with in_cluster=True calls InClusterAuthAdapter.load()."""
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    cfg = K8sProviderConfig(in_cluster=True)  # type: ignore[call-arg]
    client = K8sClient(config=cfg, logger=MagicMock())

    mock_adapter = MagicMock()
    client._in_cluster_adapter = mock_adapter

    client.load_config()

    mock_adapter.load.assert_called_once()


# ---------------------------------------------------------------------------
# api_client / core_v1 / apps_v1 / batch_v1 lazy wiring
# ---------------------------------------------------------------------------


def test_api_client_lazy_builds_on_first_access() -> None:
    """api_client property creates an ApiClient when none is pre-supplied."""
    mock_api = MagicMock()
    fake_api_client_cls = MagicMock(return_value=mock_api)

    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    cfg = K8sProviderConfig(in_cluster=False)  # type: ignore[call-arg]
    client = K8sClient(config=cfg, logger=MagicMock())

    with patch("orb.providers.k8s.infrastructure.k8s_client.load_kubeconfig"):
        with patch(
            "kubernetes.client.api_client.ApiClient",
            fake_api_client_cls,
        ):
            # Import the real module to patch the inner import

            import kubernetes.client.api_client as _api_client_mod

            orig = _api_client_mod.ApiClient
            _api_client_mod.ApiClient = fake_api_client_cls  # type: ignore[assignment]
            try:
                result = client.api_client
            finally:
                _api_client_mod.ApiClient = orig

    # The property must return an ApiClient and memoize it.
    assert result is not None
    assert client._api_client is not None


def test_core_v1_lazy_accessor_builds_once() -> None:
    """core_v1 property wraps the pre-supplied ApiClient in a CoreV1Api."""
    mock_core = MagicMock()
    mock_api_client = MagicMock()
    client = _make_client(api_client=mock_api_client)

    with patch("kubernetes.client.CoreV1Api", return_value=mock_core):
        import kubernetes.client as _kc

        orig = _kc.CoreV1Api
        _kc.CoreV1Api = MagicMock(return_value=mock_core)  # type: ignore[assignment]
        try:
            result = client.core_v1
            result2 = client.core_v1  # second access should return same object
        finally:
            _kc.CoreV1Api = orig

    # Both accesses return the same instance (lazy memoisation).
    assert result is result2


def test_apps_v1_lazy_accessor_builds_once() -> None:
    """apps_v1 property wraps the pre-supplied ApiClient in an AppsV1Api."""
    mock_api_client = MagicMock()
    client = _make_client(api_client=mock_api_client)

    import kubernetes.client as _kc

    mock_apps = MagicMock()
    orig = _kc.AppsV1Api
    _kc.AppsV1Api = MagicMock(return_value=mock_apps)  # type: ignore[assignment]
    try:
        r1 = client.apps_v1
        r2 = client.apps_v1
    finally:
        _kc.AppsV1Api = orig

    assert r1 is r2


def test_batch_v1_lazy_accessor_builds_once() -> None:
    """batch_v1 property wraps the pre-supplied ApiClient in a BatchV1Api."""
    mock_api_client = MagicMock()
    client = _make_client(api_client=mock_api_client)

    import kubernetes.client as _kc

    mock_batch = MagicMock()
    orig = _kc.BatchV1Api
    _kc.BatchV1Api = MagicMock(return_value=mock_batch)  # type: ignore[assignment]
    try:
        r1 = client.batch_v1
        r2 = client.batch_v1
    finally:
        _kc.BatchV1Api = orig

    assert r1 is r2


def test_cleanup_resets_cached_api_sub_clients() -> None:
    """cleanup() must null out core_v1, apps_v1, and batch_v1 cached instances."""
    mock_api_client = MagicMock()
    client = _make_client(api_client=mock_api_client)

    # Pre-warm all three lazy accessors.
    client._core_v1 = MagicMock()
    client._apps_v1 = MagicMock()
    client._batch_v1 = MagicMock()

    client.cleanup()

    assert client._core_v1 is None
    assert client._apps_v1 is None
    assert client._batch_v1 is None
    assert client._api_client is None


# ---------------------------------------------------------------------------
# cleanup() calls api_client.close()
# ---------------------------------------------------------------------------


def test_cleanup_calls_api_client_close() -> None:
    """cleanup() must call close() on the underlying ApiClient."""
    mock_api_client = MagicMock()
    client = _make_client(api_client=mock_api_client)

    client.cleanup()

    mock_api_client.close.assert_called_once()


def test_cleanup_idempotent() -> None:
    """Calling cleanup() twice must not raise and must call close() only once."""
    mock_api_client = MagicMock()
    client = _make_client(api_client=mock_api_client)

    client.cleanup()
    client.cleanup()  # second call — api_client is now None

    mock_api_client.close.assert_called_once()


def test_cleanup_tolerates_missing_close() -> None:
    """cleanup() must not raise when ApiClient has no close() method."""
    mock_api_client = object()  # no close attribute
    client = _make_client(api_client=mock_api_client)
    client.cleanup()  # must not raise


# ---------------------------------------------------------------------------
# call_with_auth_retry — 401 triggers one retry
# ---------------------------------------------------------------------------


def test_call_with_auth_retry_succeeds_first_time() -> None:
    """call_with_auth_retry passes through when fn succeeds on the first call."""
    mock_api_client = MagicMock()
    client = _make_client(api_client=mock_api_client)

    fn = MagicMock(return_value="ok")
    result = client.call_with_auth_retry(fn, "arg1", key="val")

    fn.assert_called_once_with("arg1", key="val")
    assert result == "ok"


def test_call_with_auth_retry_retries_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 ApiException must cause one retry after credential refresh."""
    import sys

    class _FakeApiException(Exception):
        def __init__(self, status: int) -> None:
            self.status = status
            super().__init__(f"HTTP {status}")

    fake_exceptions = SimpleNamespace(ApiException=_FakeApiException)
    monkeypatch.setitem(sys.modules, "kubernetes.client.exceptions", fake_exceptions)

    mock_api_client = MagicMock()
    client = _make_client(api_client=mock_api_client)
    # Wire a fake in-cluster adapter so the refresh path is exercised.
    mock_adapter = MagicMock()
    mock_adapter.refresh_if_stale.return_value = False
    client._in_cluster_adapter = mock_adapter

    # fn fails first with 401, then succeeds.
    fn = MagicMock(side_effect=[_FakeApiException(401), "recovered"])

    with patch("orb.providers.k8s.infrastructure.k8s_client.load_in_cluster_config") as mock_reload:
        result = client.call_with_auth_retry(fn)

    assert result == "recovered"
    assert fn.call_count == 2
    mock_reload.assert_called_once()


def test_call_with_auth_retry_does_not_retry_non_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-401 exceptions are not retried."""
    import sys

    class _FakeApiException(Exception):
        def __init__(self, status: int) -> None:
            self.status = status

    fake_exceptions = SimpleNamespace(ApiException=_FakeApiException)
    monkeypatch.setitem(sys.modules, "kubernetes.client.exceptions", fake_exceptions)

    mock_api_client = MagicMock()
    client = _make_client(api_client=mock_api_client)

    fn = MagicMock(side_effect=_FakeApiException(403))

    with pytest.raises(_FakeApiException):
        client.call_with_auth_retry(fn)

    fn.assert_called_once()


# ---------------------------------------------------------------------------
# refresh_if_stale proxies to InClusterAuthAdapter
# ---------------------------------------------------------------------------


def test_refresh_if_stale_noop_with_api_client_override() -> None:
    """refresh_if_stale() must return False when api_client was pre-supplied.

    When a pre-built ApiClient is injected (typical in unit tests) there is
    no in-cluster adapter and the method is a no-op.
    """
    mock_api_client = MagicMock()
    client = _make_client(api_client=mock_api_client)

    # adapter must be None when api_client was pre-supplied
    assert client._in_cluster_adapter is None
    assert client.refresh_if_stale() is False
