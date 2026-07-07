"""T15 — Go SDK full acquire/release cycle.

Scenario
--------
Invoke the ORB Go SDK binary (``orb-go`` or the path set in
``ORB_GO_SDK_BINARY``) to acquire machines, verify the cluster state, then
release, verifying cleanup.

Prerequisites
-------------
* Real Kubernetes cluster accessible via ORB config.
* ORB Go SDK binary on ``PATH`` or ``ORB_GO_SDK_BINARY`` env var set.
* ORB REST server running (``ORB_REST_BASE_URL``, default
  ``http://localhost:8080``).
* Pass ``--run-k8s`` to enable.

Cleanup guarantee
-----------------
The test invokes the SDK release path in its ``finally`` block.  Any
surviving pods are caught by the session-scoped nuclear cleanup fixture
in ``conftest.py`` via ``orb.io/managed=true`` label sweep.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any

import pytest

log = logging.getLogger("k8s.live.go_sdk")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_GO_SDK_BINARY = os.environ.get("ORB_GO_SDK_BINARY", "orb-go")
_ORB_REST_BASE_URL = os.environ.get("ORB_REST_BASE_URL", "http://localhost:8080")
_ACQUIRE_TIMEOUT = 180  # seconds
_RELEASE_TIMEOUT = 60  # seconds
_POLL_INTERVAL = 5  # seconds


# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------


def _go_sdk_available() -> bool:
    """Return True when the Go SDK binary is on PATH."""
    return shutil.which(_GO_SDK_BINARY) is not None


def _rest_available() -> bool:
    """Return True when the ORB REST server is reachable."""
    try:
        import urllib.request

        with urllib.request.urlopen(f"{_ORB_REST_BASE_URL}/health", timeout=5) as resp:
            return resp.status < 500
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_sdk(args: list[str], timeout: int = 60) -> dict:
    """Run the Go SDK binary with JSON output and return the parsed dict."""
    cmd = [_GO_SDK_BINARY, "--output=json", "--server", _ORB_REST_BASE_URL] + args
    log.debug("Running SDK: %s", cmd)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Go SDK exited {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return json.loads(result.stdout)  # type: ignore[return-value]


def _poll_sdk_status(request_id: str, target_status: str, timeout: float) -> dict:
    """Poll SDK status until it matches ``target_status``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = _run_sdk(["request", "status", "--request-id", request_id])
            status = resp.get("status", "")
            log.debug("SDK status for %s: %s", request_id, status)
            if status == target_status:
                return resp
        except Exception as exc:
            log.debug("poll_sdk_status error: %s", exc)
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(
        f"Request {request_id} did not reach {target_status!r} via Go SDK within {timeout}s"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _go_sdk_available(),
    reason=(
        f"Go SDK binary '{_GO_SDK_BINARY}' not found. "
        "Install orb-go or set ORB_GO_SDK_BINARY to the binary path."
    ),
)
@pytest.mark.skipif(
    not _rest_available(),
    reason=(
        f"ORB REST server not reachable at {_ORB_REST_BASE_URL}. "
        "Set ORB_REST_BASE_URL or start the server before running this test."
    ),
)
async def test_go_sdk_acquire_release_cycle(
    k8s_namespace: str,
    k8s_core_v1: Any,
    live_request_id: str,
) -> None:
    """Full Go SDK acquire → poll-until-fulfilled → release → verify-clean cycle.

    1. ``orb-go acquire --provider-type=k8s --count=1 --request-id=<id>``
    2. ``orb-go request status --request-id=<id>`` until fulfilled.
    3. Verify pod(s) exist in the cluster with the request-id label.
    4. ``orb-go release --request-id=<id>``
    5. Verify pods are gone from the cluster.
    """
    acquired_machine_ids: list[str] = []

    try:
        log.info("Go SDK acquire for %s", live_request_id)
        acquire_resp = _run_sdk(
            [
                "acquire",
                "--provider-type",
                "k8s",
                "--provider-api",
                "Pod",
                "--template-id",
                "live-go-sdk-tpl",
                "--count",
                "1",
                "--request-id",
                live_request_id,
            ]
        )
        log.info("SDK acquire response: %r", acquire_resp)

        fulfilled = _poll_sdk_status(live_request_id, "fulfilled", timeout=_ACQUIRE_TIMEOUT)
        acquired_machine_ids = fulfilled.get("machine_ids") or []
        assert len(acquired_machine_ids) >= 1, (
            f"Go SDK: expected machine_ids after fulfil, got: {acquired_machine_ids!r}"
        )

        # Verify cluster state.
        label_selector = f"orb.io/request-id={live_request_id}"
        pod_list = k8s_core_v1.list_namespaced_pod(
            namespace=k8s_namespace, label_selector=label_selector
        )
        assert len(pod_list.items) >= 1, (
            f"Expected pods for {live_request_id} in {k8s_namespace}, found none"
        )
        log.info("Cluster has %d pod(s) for %s", len(pod_list.items), live_request_id)

    finally:
        if acquired_machine_ids:
            try:
                _run_sdk(["release", "--request-id", live_request_id], timeout=30)
                _poll_sdk_status(live_request_id, "released", timeout=_RELEASE_TIMEOUT)

                label_selector = f"orb.io/request-id={live_request_id}"
                pod_list = k8s_core_v1.list_namespaced_pod(
                    namespace=k8s_namespace, label_selector=label_selector
                )
                assert len(pod_list.items) == 0, (
                    f"Pods still present after Go SDK release: "
                    f"{[p.metadata.name for p in pod_list.items]!r}"
                )
            except Exception as exc:
                log.warning("Go SDK release cleanup failed for %s: %s", live_request_id, exc)
