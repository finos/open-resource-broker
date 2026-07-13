"""Shared fixtures for kmock-backed Kubernetes handler tests.

The kmock library starts an in-process aiohttp server that emulates the
Kubernetes apiserver at the HTTP level.  All tests in this package point
the real ``kubernetes.client.ApiClient`` at the kmock URL, so the full
SDK serialisation / deserialisation stack is exercised without touching
a live cluster.

Fixture layout
--------------

* ``kmock_k8s`` (function-scoped) — a running :class:`KubernetesEmulator`
  with an associated HTTP server.  Each test gets a fresh emulator so
  there is no cross-test state.

* ``k8s_api_client`` (function-scoped) — a ``kubernetes.client.ApiClient``
  whose ``host`` is the kmock server URL.  SSL verification is disabled
  (the server is plain HTTP).

* ``k8s_client_facade`` (function-scoped) — the ORB :class:`K8sClient`
  wrapper built around ``k8s_api_client``.  Handlers accept this object
  and use it to reach ``core_v1``, ``apps_v1``, and ``batch_v1``.

* ``k8s_config`` (function-scoped) — a minimal :class:`K8sProviderConfig`
  targeting the ``orb-test`` namespace.

* ``mock_logger`` (function-scoped) — a plain :class:`unittest.mock.MagicMock`
  satisfying the :class:`LoggingPort` protocol.

* ``orb_config_dir_k8s`` (function-scoped) — a complete ORB config directory
  pointing at a kmock-backed k8s provider instance; used by delivery-surface
  tests (CLI / MCP / REST / SDK).  Sets ``ORB_CONFIG_DIR`` and tears down
  cleanly.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from kmock import KubernetesEmulator
from kmock._internal.apps import Server

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
_CONFIG_SOURCE = _PROJECT_ROOT / "config"

# Provider instance name used in delivery-surface tests.
K8S_KMOCK_PROVIDER_NAME = "k8s_kmock_test"


@pytest_asyncio.fixture()
async def kmock_k8s() -> AsyncIterator[KubernetesEmulator]:
    """Function-scoped KubernetesEmulator + HTTP server.

    Using a fresh instance per test guarantees that no recorded requests,
    pre-loaded objects, or server state from one test bleed into another.
    The emulator is a full in-memory K8s apiserver (CREATE / PATCH / DELETE
    tracked, LIST / GET / WATCH served from the in-memory store).
    """
    async with KubernetesEmulator() as emulator, Server(emulator):
        yield emulator


@pytest.fixture()
def k8s_api_client(kmock_k8s: KubernetesEmulator):  # type: ignore[return]
    """A kubernetes.client.ApiClient whose host points at the kmock server."""
    # kubernetes is an optional extra; imports are deferred so pyright does
    # not raise errors when the extra is absent at type-check time.
    import kubernetes.client as _kc

    ApiClient = _kc.ApiClient  # type: ignore[attr-defined]
    Configuration = _kc.Configuration  # type: ignore[attr-defined]

    cfg = Configuration()
    cfg.host = str(kmock_k8s.url).rstrip("/")
    cfg.verify_ssl = False
    return ApiClient(configuration=cfg)


@pytest.fixture()
def k8s_client_facade(k8s_api_client):  # type: ignore[return]
    """The ORB K8sClient facade wrapping the kmock-pointed ApiClient."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient

    config = K8sProviderConfig(namespace="orb-test")  # type: ignore[call-arg]
    logger = MagicMock()
    return K8sClient(config=config, logger=logger, api_client=k8s_api_client)


@pytest.fixture()
def k8s_config():  # type: ignore[return]
    """Minimal K8sProviderConfig targeting the orb-test namespace."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig

    return K8sProviderConfig(namespace="orb-test")  # type: ignore[call-arg]


@pytest.fixture()
def mock_logger() -> MagicMock:
    """A MagicMock satisfying the LoggingPort protocol."""
    return MagicMock()


# ---------------------------------------------------------------------------
# orb_config_dir_k8s — delivery-surface tests (CLI / MCP / REST / SDK)
# ---------------------------------------------------------------------------


@pytest.fixture()
def orb_config_dir_k8s(tmp_path, kmock_k8s):
    """Generate a complete ORB config directory pointing at the kmock k8s provider.

    Writes a ``config.json`` that declares a single k8s provider instance
    (``k8s_kmock_test``) with ``in_cluster: false`` and an explicit
    ``namespace: orb-test``.  Sets ``ORB_CONFIG_DIR`` for the test and clears
    it on teardown.

    Depends on ``kmock_k8s`` so each test gets a fresh emulator.

    Yields the Path to the config directory.
    """
    from orb.infrastructure.di.container import reset_container
    from tests.utilities.reset_singletons import reset_all_singletons

    reset_container()
    reset_all_singletons()

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_data = {
        "scheduler": {
            "type": "hostfactory",
            "config_root": str(config_dir),
        },
        "provider": {
            "providers": [
                {
                    "name": K8S_KMOCK_PROVIDER_NAME,
                    "type": "k8s",
                    "enabled": True,
                    "default": True,
                    "config": {
                        # in_cluster=True passes the empty-config guard in
                        # create_k8s_strategy (False is falsy and would trigger
                        # the "no cluster-targeting info" error).  The actual
                        # K8sClient is replaced post-bootstrap by
                        # _inject_kmock_factory before any network calls are made
                        # (initialize() is a no-op — it sets _initialized=True only).
                        "in_cluster": True,
                        "namespace": "orb-test",
                        "watch_enabled": False,
                        "orphan_gc_enabled": False,
                        "metrics_enabled": False,
                    },
                }
            ]
        },
        "storage": {
            "strategy": "json",
            "default_storage_path": str(tmp_path / "data"),
            "json_strategy": {
                "storage_type": "single_file",
                "base_path": str(tmp_path / "data"),
                "filenames": {"single_file": "request_database.json"},
            },
        },
    }
    with open(config_dir / "config.json", "w") as f:
        json.dump(config_data, f, indent=2)

    # Copy k8s_templates.json into the config dir so the template loader finds them.
    k8s_tpl_src = _CONFIG_SOURCE / "k8s_templates.json"
    if k8s_tpl_src.exists():
        shutil.copy2(k8s_tpl_src, config_dir / "k8s_templates.json")

    default_src = _CONFIG_SOURCE / "default_config.json"
    if default_src.exists():
        shutil.copy2(default_src, config_dir / "default_config.json")

    os.environ["ORB_CONFIG_DIR"] = str(config_dir)

    yield config_dir

    os.environ.pop("ORB_CONFIG_DIR", None)
    from orb.infrastructure.di.container import reset_container
    from tests.utilities.reset_singletons import reset_all_singletons

    reset_container()
    reset_all_singletons()
