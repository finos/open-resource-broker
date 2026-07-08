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
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from kmock import KubernetesEmulator
from kmock._internal.apps import Server


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
    import kubernetes.client as _kc  # noqa: PLC0415

    ApiClient = _kc.ApiClient  # type: ignore[attr-defined]
    Configuration = _kc.Configuration  # type: ignore[attr-defined]

    cfg = Configuration()
    cfg.host = str(kmock_k8s.url).rstrip("/")
    cfg.verify_ssl = False
    return ApiClient(configuration=cfg)


@pytest.fixture()
def k8s_client_facade(k8s_api_client):  # type: ignore[return]
    """The ORB K8sClient facade wrapping the kmock-pointed ApiClient."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig  # noqa: PLC0415
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient  # noqa: PLC0415

    config = K8sProviderConfig(namespace="orb-test")  # type: ignore[call-arg]
    logger = MagicMock()
    return K8sClient(config=config, logger=logger, api_client=k8s_api_client)


@pytest.fixture()
def k8s_config():  # type: ignore[return]
    """Minimal K8sProviderConfig targeting the orb-test namespace."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig  # noqa: PLC0415

    return K8sProviderConfig(namespace="orb-test")  # type: ignore[call-arg]


@pytest.fixture()
def mock_logger() -> MagicMock:
    """A MagicMock satisfying the LoggingPort protocol."""
    return MagicMock()
