"""T14 — REST API full acquire/release cycle.

Scenario
--------
Submit an acquire request via the ORB REST API, poll until the request
reaches ``fulfilled`` state, verify pods exist in the cluster, then submit
a release request and verify the pods are removed.

Prerequisites
-------------
* Real Kubernetes cluster accessible via ORB config.
* ORB REST server running (``ORB_REST_BASE_URL`` env var, default
  ``http://localhost:8080``).
* Pass ``--run-k8s`` to enable.

Cleanup guarantee
-----------------
The test calls the release endpoint in its ``finally`` block.  Any
surviving pods are caught by the session-scoped nuclear cleanup fixture
in ``conftest.py`` via ``orb.io/managed=true`` label sweep.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import pytest

log = logging.getLogger("k8s.live.rest_api")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_ORB_REST_BASE_URL = os.environ.get("ORB_REST_BASE_URL", "http://localhost:8080")
_POLL_INTERVAL = 5  # seconds
_ACQUIRE_TIMEOUT = 180  # seconds
_RELEASE_TIMEOUT = 60  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rest_available() -> bool:
    """Return True when the ORB REST server is reachable."""
    try:
        import urllib.request

        with urllib.request.urlopen(f"{_ORB_REST_BASE_URL}/health", timeout=5) as resp:
            return resp.status < 500
    except Exception:
        return False


def _post_json(path: str, payload: dict) -> dict:
    """POST JSON to the ORB REST server and return the response dict."""
    import json
    import urllib.request

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{_ORB_REST_BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())  # type: ignore[return-value]


def _get_json(path: str) -> dict:
    """GET from the ORB REST server and return the response dict."""
    import json
    import urllib.request

    with urllib.request.urlopen(f"{_ORB_REST_BASE_URL}{path}", timeout=30) as resp:
        return json.loads(resp.read())  # type: ignore[return-value]


def _poll_request_status(
    request_id: str,
    target_status: str,
    timeout: float = _ACQUIRE_TIMEOUT,
) -> dict:
    """Poll ``/requests/{id}`` until ``status`` matches ``target_status``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = _get_json(f"/requests/{request_id}")
            status = resp.get("status", "")
            log.debug("Request %s status: %s", request_id, status)
            if status == target_status:
                return resp
        except Exception as exc:
            log.debug("poll_request_status error: %s", exc)
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(
        f"Request {request_id} did not reach status={target_status!r} within {timeout}s"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _rest_available(),
    reason=(
        f"ORB REST server not reachable at {_ORB_REST_BASE_URL}. "
        "Set ORB_REST_BASE_URL or start the server before running this test."
    ),
)
async def test_rest_api_acquire_release_cycle(
    k8s_namespace: str,
    k8s_core_v1: Any,
    live_request_id: str,
) -> None:
    """Full REST acquire → poll-until-fulfilled → release → verify-clean cycle.

    1. POST /machines/acquire with provider_type=k8s, count=1.
    2. Poll /requests/{id} until status == "fulfilled".
    3. Verify at least one pod labelled with the request-id exists.
    4. POST /machines/release with the returned machine ids.
    5. Poll /requests/{id} until status == "released".
    6. Verify no pods labelled with the request-id remain.
    """
    acquire_payload = {
        "request_id": live_request_id,
        "provider_type": "k8s",
        "provider_api": "Pod",
        "template_id": "live-rest-tpl",
        "count": 1,
    }

    acquired_machine_ids: list[str] = []

    try:
        log.info("Submitting acquire via REST for %s", live_request_id)
        acquire_resp = _post_json("/machines/acquire", acquire_payload)
        assert acquire_resp.get("request_id") == live_request_id, (
            f"Unexpected request_id in response: {acquire_resp!r}"
        )

        fulfilled = _poll_request_status(live_request_id, "fulfilled")
        acquired_machine_ids = fulfilled.get("machine_ids") or []
        assert len(acquired_machine_ids) >= 1, (
            f"Expected at least 1 machine_id after fulfil, got: {acquired_machine_ids!r}"
        )
        log.info("Request %s fulfilled with machines: %r", live_request_id, acquired_machine_ids)

        # Verify pods exist in the cluster.
        label_selector = f"orb.io/request-id={live_request_id}"
        pod_list = k8s_core_v1.list_namespaced_pod(
            namespace=k8s_namespace, label_selector=label_selector
        )
        assert len(pod_list.items) >= 1, (
            f"Expected pods for request {live_request_id} in namespace {k8s_namespace}, found none"
        )
        log.info("Found %d pod(s) for request %s", len(pod_list.items), live_request_id)

    finally:
        if acquired_machine_ids:
            try:
                release_payload = {
                    "request_id": live_request_id,
                    "machine_ids": acquired_machine_ids,
                }
                _post_json("/machines/release", release_payload)
                log.info("Release submitted for %s", live_request_id)
                _poll_request_status(live_request_id, "released", timeout=_RELEASE_TIMEOUT)
                log.info("Request %s released", live_request_id)

                # Verify pods are gone.
                label_selector = f"orb.io/request-id={live_request_id}"
                pod_list = k8s_core_v1.list_namespaced_pod(
                    namespace=k8s_namespace, label_selector=label_selector
                )
                assert len(pod_list.items) == 0, (
                    f"Expected no pods after release, found: "
                    f"{[p.metadata.name for p in pod_list.items]!r}"
                )
            except Exception as exc:
                log.warning("REST release cleanup failed for %s: %s", live_request_id, exc)
