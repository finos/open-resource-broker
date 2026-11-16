"""SwiftSandbox -> ORB machine status for the KubeSwiftSandbox handler."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from orb.providers.k8s.infrastructure.handlers.kubeswift.sandbox_status import (
    fulfilment_for,
    reportable_ip,
    sandbox_machine_status,
)


def _sandbox(
    phase: Optional[str] = None,
    guest_running: Optional[bool] = None,
    deleting: bool = False,
    network: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {"metadata": {"name": "m"}, "status": {}}
    if phase:
        obj["status"]["phase"] = phase
    if guest_running is not None:
        obj["status"]["conditions"] = [
            {"type": "GuestRunning", "status": "True" if guest_running else "False"}
        ]
    if deleting:
        obj["metadata"]["deletionTimestamp"] = "2026-11-05T00:00:00Z"
    if network:
        obj["status"]["network"] = network
    return obj


@pytest.mark.parametrize(
    ("obj", "want"),
    [
        (_sandbox(), "pending"),
        (_sandbox("Pending"), "pending"),
        (_sandbox("Materializing"), "pending"),
        (_sandbox("Running"), "launching"),
        (_sandbox("Running", guest_running=False), "launching"),
        (_sandbox("Running", guest_running=True), "running"),
        (_sandbox("Running", guest_running=True, deleting=True), "shutting-down"),
        (_sandbox("Exporting"), "shutting-down"),
        (_sandbox("Completed"), "terminated"),
        (_sandbox("Failed"), "failed"),
        (_sandbox("Bogus"), "unknown"),
    ],
)
def test_machine_status(obj: dict[str, Any], want: str) -> None:
    assert sandbox_machine_status(obj)[0] == want


def test_pod_scope_ip_is_never_reported() -> None:
    obj = _sandbox("Running", network={"primaryIP": "192.168.99.10", "primaryIPScope": "Pod"})
    assert reportable_ip(obj) is None


def test_network_scope_ip_is_reported() -> None:
    obj = _sandbox("Running", network={"primaryIP": "10.20.0.5", "primaryIPScope": "Network"})
    assert reportable_ip(obj) == "10.20.0.5"


def _inst(status: str, reason: Optional[str] = None) -> dict[str, Any]:
    return {"status": status, "status_reason": reason}


def test_fulfilment_states() -> None:
    assert fulfilment_for([_inst("running")] * 2, 2).state == "fulfilled"
    assert fulfilment_for([_inst("running"), _inst("launching")], 2).state == "in_progress"
    failed = fulfilment_for([_inst("failed", "ImagePullFailed")] * 2, 2)
    assert failed.state == "failed"
    assert failed.diagnostic is not None
    assert "ImagePullFailed" in failed.diagnostic.summary
    partial = fulfilment_for([_inst("running"), _inst("failed", "x")], 2)
    assert partial.state == "partial"
    assert partial.final is False
