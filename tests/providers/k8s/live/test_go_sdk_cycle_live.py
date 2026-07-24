"""T15 — Go SDK full acquire/release cycle.

Scenario
--------
Drive the ORB Go SDK through a command-line wrapper to acquire machines,
verify the cluster state, then release, verifying cleanup.

Prerequisites
-------------
* Real Kubernetes cluster accessible via ORB config.
* A Go toolchain (``go``) on PATH.  The ``go_sdk_cli`` fixture builds a small
  CLI over the in-repo Go SDK (``sdk/go``); the test skips when Go is absent.
* Pass ``--run-k8s`` to enable.

The ORB REST server is provided by the session-scoped ``orb_rest_server``
fixture (see ``conftest.py``); no external server needs to be started.

Cleanup guarantee
-----------------
The test invokes the SDK release path in its ``finally`` block.  Any surviving
pods are caught by the session-scoped nuclear cleanup fixture in
``conftest.py`` via the ``orb.io/managed=true`` label sweep.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any

import pytest

log = logging.getLogger("k8s.live.go_sdk")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_TEMPLATE_ID = "k8s-pod-example"
_ACQUIRE_TIMEOUT = 180  # seconds
_RELEASE_TIMEOUT = 60  # seconds
_POLL_INTERVAL = 5  # seconds
_FULFILLED_STATES = {"complete", "running"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cli(binary: str, server: Any, args: list[str], timeout: int = 60) -> dict:
    """Run the Go SDK CLI wrapper and return its parsed JSON output."""
    cmd = [binary, "--server", server.base_url]
    if getattr(server, "token", None):
        cmd += ["--token", server.token]
    cmd += args
    log.debug("Running Go SDK CLI: %s", cmd)
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Go SDK CLI exited {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return json.loads(result.stdout)  # type: ignore[return-value]


def _poll_status(
    binary: str, server: Any, request_id: str, target_states: set[str], timeout: float
) -> dict:
    """Poll SDK status until the request reaches one of ``target_states`` with machines."""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            last = _run_cli(binary, server, ["status", "--request-id", request_id])
            status = last.get("status", "")
            log.debug("SDK status for %s: %s", request_id, status)
            if status in target_states and last.get("machine_ids"):
                return last
        except Exception as exc:  # noqa: BLE001 — transient poll errors are retried
            log.debug("poll error: %s", exc)
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(
        f"Request {request_id} did not reach {target_states} via Go SDK within {timeout}s "
        f"(last status={last.get('status')!r})"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_go_sdk_acquire_release_cycle(
    go_sdk_cli: str | None,
    orb_rest_server: Any,
    k8s_namespace: str,
    k8s_core_v1: Any,
) -> None:
    """Full Go SDK acquire → poll-until-complete → release → verify-clean cycle.

    1. ``orb-go-cli acquire --template k8s-pod-example --count 1``
    2. ``orb-go-cli status --request-id <id>`` until complete.
    3. Verify pod(s) exist in the cluster with the request-id label.
    4. ``orb-go-cli release --machine-id <id>``
    5. Verify pods are gone from the cluster.
    """
    if go_sdk_cli is None:
        pytest.skip(
            "Go toolchain not available to build the Go SDK CLI. "
            "Install Go (or set ORB_GO_SDK_BINARY) to run the Go SDK cycle test."
        )

    acquire_resp = _run_cli(
        go_sdk_cli,
        orb_rest_server,
        ["acquire", "--template", _TEMPLATE_ID, "--count", "1"],
    )
    request_id = acquire_resp.get("request_id")
    assert request_id, f"No request_id from Go SDK acquire: {acquire_resp!r}"
    log.info("Go SDK acquire submitted: %s", request_id)

    acquired_machine_ids: list[str] = []
    try:
        record = _poll_status(
            go_sdk_cli, orb_rest_server, request_id, _FULFILLED_STATES, _ACQUIRE_TIMEOUT
        )
        acquired_machine_ids = record.get("machine_ids") or []
        assert acquired_machine_ids, f"No machine ids from Go SDK status: {record!r}"

        label_selector = f"orb.io/request-id={request_id}"
        pod_list = k8s_core_v1.list_namespaced_pod(
            namespace=k8s_namespace, label_selector=label_selector
        )
        assert len(pod_list.items) >= 1, (
            f"Expected pods for {request_id} in {k8s_namespace}, found none"
        )
        log.info("Cluster has %d pod(s) for %s", len(pod_list.items), request_id)

    finally:
        if acquired_machine_ids:
            try:
                release_args = ["release"]
                for mid in acquired_machine_ids:
                    release_args += ["--machine-id", mid]
                _run_cli(go_sdk_cli, orb_rest_server, release_args, timeout=30)

                label_selector = f"orb.io/request-id={request_id}"
                deadline = time.monotonic() + _RELEASE_TIMEOUT
                remaining = None
                while time.monotonic() < deadline:
                    pod_list = k8s_core_v1.list_namespaced_pod(
                        namespace=k8s_namespace, label_selector=label_selector
                    )
                    remaining = [p.metadata.name for p in pod_list.items]
                    if not remaining:
                        break
                    time.sleep(_POLL_INTERVAL)
                assert not remaining, f"Pods still present after Go SDK release: {remaining!r}"
            except Exception as exc:  # noqa: BLE001 — cleanup must not mask the result
                log.warning("Go SDK release cleanup failed for %s: %s", request_id, exc)
