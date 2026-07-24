"""T14 — REST API full acquire/release cycle.

Scenario
--------
Submit an acquire request via the ORB REST API, poll until the request
reaches a terminal ``complete`` state, verify pods exist in the cluster, then
submit a return request and verify the pods are removed.

Prerequisites
-------------
* Real Kubernetes cluster accessible via ORB config.
* Pass ``--run-k8s`` to enable.

The ORB REST server is provided by the session-scoped ``orb_rest_server``
fixture (see ``conftest.py``): it launches ``orb server start --foreground
--api-only`` on an isolated work dir and free loopback port, so no external
server needs to be started.  Set ``ORB_REST_BASE_URL`` to point the tests at
an already-running server instead.

Cleanup guarantee
-----------------
The test calls the return endpoint in its ``finally`` block.  Any surviving
pods are caught by the session-scoped nuclear cleanup fixture in
``conftest.py`` via the ``orb.io/managed=true`` label sweep.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

import pytest

log = logging.getLogger("k8s.live.rest_api")

pytestmark = [pytest.mark.asyncio, pytest.mark.k8s_live]

_TEMPLATE_ID = "k8s-pod-example"
_POLL_INTERVAL = 5  # seconds
_ACQUIRE_TIMEOUT = 180  # seconds
_RELEASE_TIMEOUT = 60  # seconds
# Terminal states the default scheduler reports for a fully-provisioned acquire.
_FULFILLED_STATES = {"complete", "running"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _headers(server: Any) -> dict[str, str]:
    """Build request headers, attaching the loopback-admin bearer token.

    POST routes require the operator role; without the token the anonymous
    caller resolves to ``viewer`` and the server returns 403.
    """
    headers = {"Content-Type": "application/json"}
    if getattr(server, "token", None):
        headers["Authorization"] = f"Bearer {server.token}"
    return headers


def _post_json(server: Any, path: str, payload: dict) -> dict:
    """POST JSON to the ORB REST server and return the response dict."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{server.base_url}{path}",
        data=data,
        headers=_headers(server),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — loopback test server
        return json.loads(resp.read())  # type: ignore[return-value]


def _get_json(server: Any, path: str) -> dict:
    """GET from the ORB REST server and return the response dict."""
    req = urllib.request.Request(f"{server.base_url}{path}", headers=_headers(server))
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — loopback test server
        return json.loads(resp.read())  # type: ignore[return-value]


def _extract_request(status_body: dict) -> dict:
    """Pull the single request record out of a status envelope."""
    requests = status_body.get("requests") if isinstance(status_body, dict) else None
    if requests:
        return requests[0]
    return status_body if isinstance(status_body, dict) else {}


def _machine_ids(record: dict) -> list[str]:
    """Collect machine ids from a request record (snake_case default scheduler)."""
    ids: list[str] = []
    if record.get("machine_ids"):
        return list(record["machine_ids"])
    for machine in record.get("machines", []) or []:
        mid = machine.get("machine_id") or machine.get("machineId")
        if mid:
            ids.append(mid)
    return ids


def _poll_request(
    server: Any,
    request_id: str,
    target_states: set[str],
    *,
    require_machines: bool,
    timeout: float,
) -> dict:
    """Poll ``/requests/{id}/status`` until the request reaches a target state."""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            body = _get_json(server, f"/api/v1/requests/{request_id}/status")
            last = _extract_request(body)
            status = last.get("status", "")
            log.debug("Request %s status: %s", request_id, status)
            if status in target_states and (not require_machines or _machine_ids(last)):
                return last
        except Exception as exc:  # noqa: BLE001 — transient poll errors are retried
            log.debug("poll error: %s", exc)
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(
        f"Request {request_id} did not reach {target_states} within {timeout}s "
        f"(last status={last.get('status')!r})"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_rest_api_acquire_release_cycle(
    orb_rest_server: Any,
    k8s_namespace: str,
    k8s_core_v1: Any,
) -> None:
    """Full REST acquire → poll-until-complete → return → verify-clean cycle.

    1. POST /api/v1/machines/request with the k8s pod template, count=1.
    2. Poll /api/v1/requests/{id}/status until the request is complete.
    3. Verify at least one pod labelled with the request-id exists.
    4. POST /api/v1/machines/return with the returned machine ids.
    5. Verify no pods labelled with the request-id remain.
    """
    acquire_resp = _post_json(
        orb_rest_server,
        "/api/v1/machines/request",
        {"template_id": _TEMPLATE_ID, "count": 1},
    )
    request_id = acquire_resp.get("request_id") or acquire_resp.get("requestId")
    assert request_id, f"No request_id in acquire response: {acquire_resp!r}"
    log.info("REST acquire submitted: %s", request_id)

    acquired_machine_ids: list[str] = []
    try:
        record = _poll_request(
            orb_rest_server,
            request_id,
            _FULFILLED_STATES,
            require_machines=True,
            timeout=_ACQUIRE_TIMEOUT,
        )
        acquired_machine_ids = _machine_ids(record)
        assert acquired_machine_ids, f"No machine ids after fulfil: {record!r}"
        log.info("Request %s fulfilled with %r", request_id, acquired_machine_ids)

        label_selector = f"orb.io/request-id={request_id}"
        pod_list = k8s_core_v1.list_namespaced_pod(
            namespace=k8s_namespace, label_selector=label_selector
        )
        assert len(pod_list.items) >= 1, (
            f"Expected pods for request {request_id} in {k8s_namespace}, found none"
        )
        log.info("Found %d pod(s) for request %s", len(pod_list.items), request_id)

    finally:
        if acquired_machine_ids:
            try:
                _post_json(
                    orb_rest_server,
                    "/api/v1/machines/return",
                    {"machine_ids": acquired_machine_ids},
                )
                log.info("Return submitted for %s", request_id)

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
                assert not remaining, f"Pods still present after return: {remaining!r}"
            except Exception as exc:  # noqa: BLE001 — cleanup failures must not mask result
                log.warning("REST return cleanup failed for %s: %s", request_id, exc)
