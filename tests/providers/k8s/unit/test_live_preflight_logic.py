"""Unit tests for the live-k8s pre-flight logic in ``tests/providers/k8s/live/conftest.py``.

The live pre-flight probes the cluster with a bare ``list_namespace`` call and
must recover from a *stale exec-token* 401 by forcing the exec credential
plugin (``aws eks get-token`` etc.) to genuinely re-mint.  The pinned kubernetes
SDK's ``ExecProvider`` does no token caching — it re-execs the plugin on every
``load_kube_config`` call — so recovery is achieved simply by issuing a fresh
``load_kube_config`` + new client on retry, not by touching any disk cache.

These tests load the live conftest by file path (conftest modules are not
importable via the normal package path) and exercise the pure recovery logic
without any real cluster.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import call, patch

import pytest

_LIVE_CONFTEST = Path(__file__).resolve().parents[1] / "live" / "conftest.py"


def _load_live_conftest() -> ModuleType:
    """Import the live conftest module from its file path."""
    spec = importlib.util.spec_from_file_location("k8s_live_conftest_under_test", _LIVE_CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def conftest_mod() -> ModuleType:
    return _load_live_conftest()


# ---------------------------------------------------------------------------
# Happy path: a single fresh load on the first attempt
# ---------------------------------------------------------------------------


def test_preflight_loads_fresh_once_on_success(conftest_mod: ModuleType) -> None:
    """A clean run issues exactly one fresh ``load_kube_config`` + client call.

    No disk-cache manipulation is performed: the SDK's ``ExecProvider`` re-execs
    the plugin on every load, so a fresh load is all that is needed.
    """
    with patch.object(conftest_mod, "_fresh_load_and_list_namespaces") as mock_load:
        ok, exc = conftest_mod._preflight_probe_cluster("/path/kubeconfig", "ctx")

    assert ok is True
    assert exc is None
    mock_load.assert_called_once_with("/path/kubeconfig", "ctx")


# ---------------------------------------------------------------------------
# 401 recovery: a genuinely fresh load on retry
# ---------------------------------------------------------------------------


def test_preflight_401_forces_reexec_via_fresh_load_retry(
    conftest_mod: ModuleType,
) -> None:
    """On a 401, a NEW fresh load is issued so the (cache-less) exec plugin re-mints.

    Retrying in-process with the same already-loaded config/ExecProvider would
    reuse the stale token, so the retry must build a genuinely fresh
    ``load_kube_config`` + new client so the exec plugin re-execs and re-mints.
    """
    load_calls: list[str] = []

    def _fake_load(kubeconfig_path: str | None, context: str | None) -> None:
        load_calls.append("call")
        if len(load_calls) == 1:
            raise RuntimeError("(401) Unauthorized")
        # second (fresh) load succeeds
        return None

    with patch.object(
        conftest_mod, "_fresh_load_and_list_namespaces", side_effect=_fake_load
    ) as mock_load:
        ok, exc = conftest_mod._preflight_probe_cluster("/kc", "ctx")

    assert ok is True
    assert exc is None
    # A genuinely fresh load was issued on retry (two distinct load invocations).
    assert mock_load.call_count == 2
    mock_load.assert_has_calls([call("/kc", "ctx"), call("/kc", "ctx")])


def test_preflight_non_401_error_does_not_retry(conftest_mod: ModuleType) -> None:
    """A non-401 failure surfaces immediately without a second attempt.

    Only a stale-token 401 warrants the re-exec recovery; other failures
    (network, RBAC 403, config error) should fail fast so the operator sees the
    real cause instead of a misleading double attempt.
    """
    boom = RuntimeError("(500) apiserver exploded")
    with patch.object(
        conftest_mod, "_fresh_load_and_list_namespaces", side_effect=boom
    ) as mock_load:
        ok, exc = conftest_mod._preflight_probe_cluster("/kc", "ctx")

    assert ok is False
    assert exc is boom
    mock_load.assert_called_once()


def test_preflight_persistent_401_gives_up_after_one_retry(conftest_mod: ModuleType) -> None:
    """A persistent 401 is retried once then reported as failure (bounded recovery)."""
    persistent = RuntimeError("(401) Unauthorized")
    with patch.object(
        conftest_mod, "_fresh_load_and_list_namespaces", side_effect=persistent
    ) as mock_load:
        ok, exc = conftest_mod._preflight_probe_cluster("/kc", "ctx")

    assert ok is False
    assert exc is persistent
    # Two load attempts (initial + one 401 retry); bounded.
    assert mock_load.call_count == 2
