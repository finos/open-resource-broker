"""A RunInstances request is not complete until its machines are running."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.providers.aws.mocked.conftest import _make_logger, _make_moto_aws_client
from tests.providers.aws.mocked.test_cli_onmoto import _make_patched_initialize, _run_orb_cli
from tests.shared.response_helpers import extract_request_id

pytestmark = [pytest.mark.moto, pytest.mark.cli]


def _cli(initialize, args: list[str]) -> dict:
    from orb.bootstrap import Application

    with patch.object(Application, "initialize", initialize):
        return _run_orb_cli(args)


def _machines(initialize) -> list[dict]:
    return _cli(initialize, ["machines", "list"])["machines"]


def test_request_completes_only_with_running_machines(orb_config_dir, moto_aws):
    initialize = _make_patched_initialize(_make_moto_aws_client(), _make_logger())

    created = _cli(
        initialize,
        ["machines", "request", "--template", "RunInstances-OnDemand", "--count", "1"],
    )
    request_id = extract_request_id(created)
    assert request_id

    # Right after creation the instances are still pending, so the request
    # must not be complete yet.
    listed = _cli(initialize, ["requests", "list"])
    stored = next(
        r for r in listed["requests"] if (r.get("request_id") or r.get("requestId")) == request_id
    )
    pending = [m for m in _machines(initialize) if m["status"] == "pending"]
    assert not (stored["status"] == "complete" and pending), json.dumps(stored)

    # The status read syncs the machines and settles the request.
    status = _cli(initialize, ["requests", "status", request_id])
    assert status["requests"][0]["status"] == "complete"
    # HostFactory contract: complete means every machine succeeded.
    hf_machines = status["requests"][0]["machines"]
    assert hf_machines
    assert all(m["result"] == "succeed" for m in hf_machines), json.dumps(status)

    machines = _machines(initialize)
    assert machines
    assert all(m["status"] == "running" for m in machines)
    assert all(m["privateIp"] for m in machines)
    assert all(m["imageId"] not in ("", "unknown", None) for m in machines)

    shown = _cli(initialize, ["machines", "show", machines[0]["machineId"]])
    assert shown.get("machine", shown)["status"] == "running"
