"""Unit tests for :class:`K8sClient` — token refresh and cleanup behaviour.

Backfill coverage added in Group T1:
* load_config() with in_cluster=False + a valid kubeconfig path
* load_config() with in_cluster=False + an invalid kubeconfig path (raises K8sAuthError)
* api_client lazy-wiring (property builds an ApiClient on first access)
* core_v1, apps_v1, batch_v1 lazy accessors
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# force_token_refresh — 401-recovery credential re-mint
# ---------------------------------------------------------------------------


def test_force_token_refresh_noop_with_injected_client() -> None:
    """force_token_refresh() is a no-op when an ApiClient was injected.

    An injected client (unit-test path) owns no config ORB loaded, so there is
    nothing to reload — the method must return False without touching the SDK.
    """
    client = _make_client(api_client=MagicMock())
    with patch("orb.providers.k8s.infrastructure.k8s_client.load_kubeconfig") as mock_lkc:
        with patch(
            "orb.providers.k8s.infrastructure.k8s_client.load_in_cluster_config"
        ) as mock_lic:
            # _live_client_configuration returns None because the injected mock's
            # ``configuration`` attribute is not what ORB built; force that path.
            with patch.object(client, "_live_client_configuration", return_value=None):
                assert client.force_token_refresh() is False
    mock_lkc.assert_not_called()
    mock_lic.assert_not_called()


def test_force_token_refresh_kubeconfig_reloads_live_config() -> None:
    """kubeconfig auth: 401 recovery re-runs load_kubeconfig into the LIVE config.

    The pinned kubernetes SDK's ``ExecProvider`` does no token caching — it
    re-execs the plugin (``aws eks get-token``) on every ``load_kube_config``
    call — so simply re-running the load re-mints a fresh token.  No disk-cache
    manipulation is performed (clearing ``~/.kube/cache/token`` would only
    perturb a co-located kubectl and does nothing for this SDK).  The refreshed
    credential must land on the live ApiClient's own Configuration
    (``client_configuration=``) rather than only the global default — otherwise
    the in-flight client would never see it.
    """
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    cfg = K8sProviderConfig(in_cluster=False)  # type: ignore[call-arg]
    client = K8sClient(config=cfg, logger=MagicMock())

    live_config = object()
    with patch.object(client, "_live_client_configuration", return_value=live_config):
        with patch("orb.providers.k8s.infrastructure.k8s_client.load_kubeconfig") as mock_lkc:
            result = client.force_token_refresh()

    assert result is True
    mock_lkc.assert_called_once()
    # The refreshed credential must target the LIVE client configuration.
    assert mock_lkc.call_args.kwargs["client_configuration"] is live_config


def test_force_token_refresh_coalesces_concurrent_401s() -> None:
    """Concurrent 401-driven refreshes collapse into a SINGLE re-mint.

    On a mass-401 (e.g. ~50 pod-create / orphan-GC workers) every worker calls
    ``force_token_refresh`` at once.  A lock + debounce window must coalesce the
    stampede: the first caller re-execs the plugin, and the others — which
    acquire the lock and find a just-completed refresh — reuse the fresh token
    already sitting on the shared live Configuration.  We assert the underlying
    ``load_kubeconfig`` (the re-exec seam) ran exactly once across N threads.
    """
    import threading

    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    cfg = K8sProviderConfig(in_cluster=False)  # type: ignore[call-arg]
    client = K8sClient(config=cfg, logger=MagicMock())

    live_config = object()
    start = threading.Barrier(24)
    results: list[bool] = []
    results_lock = threading.Lock()

    def _worker() -> None:
        start.wait()
        outcome = client.force_token_refresh()
        with results_lock:
            results.append(outcome)

    with patch.object(client, "_live_client_configuration", return_value=live_config):
        with patch("orb.providers.k8s.infrastructure.k8s_client.load_kubeconfig") as mock_lkc:
            threads = [threading.Thread(target=_worker) for _ in range(24)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

    # Every worker reports success (either it re-minted or reused the fresh token)...
    assert results == [True] * 24
    # ...but the expensive re-exec happened exactly once within the window.
    mock_lkc.assert_called_once()
    assert mock_lkc.call_args.kwargs["client_configuration"] is live_config


def test_force_token_refresh_re_mints_again_after_debounce_window() -> None:
    """A refresh outside the debounce window re-mints again (not permanently skipped).

    The debounce only coalesces a near-simultaneous burst; a genuinely later
    401 (past the window) must still trigger a fresh re-exec.
    """
    from orb.providers.k8s.infrastructure import k8s_client as k8s_client_mod
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    cfg = K8sProviderConfig(in_cluster=False)  # type: ignore[call-arg]
    client = K8sClient(config=cfg, logger=MagicMock())

    live_config = object()
    fake_clock = {"now": 1000.0}

    def _fake_monotonic() -> float:
        return fake_clock["now"]

    with patch.object(client, "_live_client_configuration", return_value=live_config):
        with patch("orb.providers.k8s.infrastructure.k8s_client.load_kubeconfig") as mock_lkc:
            with patch.object(k8s_client_mod.time, "monotonic", _fake_monotonic):
                assert client.force_token_refresh() is True
                # Advance well past the debounce window.
                fake_clock["now"] += k8s_client_mod._TOKEN_REFRESH_DEBOUNCE_SECONDS + 1.0
                assert client.force_token_refresh() is True

    assert mock_lkc.call_count == 2


def test_force_token_refresh_in_cluster_reloads_live_config() -> None:
    """in-cluster auth: 401 recovery reloads the SA token into the live config."""
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    cfg = K8sProviderConfig(in_cluster=True)  # type: ignore[call-arg]
    client = K8sClient(config=cfg, logger=MagicMock())
    # in_cluster=True builds an adapter; keep it (do not inject api_client).
    assert client._in_cluster_adapter is not None

    live_config = object()
    with patch.object(client, "_live_client_configuration", return_value=live_config):
        with patch(
            "orb.providers.k8s.infrastructure.k8s_client.load_in_cluster_config"
        ) as mock_lic:
            result = client.force_token_refresh()

    assert result is True
    mock_lic.assert_called_once()
    assert mock_lic.call_args.kwargs["client_configuration"] is live_config
