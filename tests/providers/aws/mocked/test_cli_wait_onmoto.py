"""Waiting on a machine request settles with the machines running.

DescribeInstances reports the instances as pending for the first few calls so
that the request stays in progress until a status poll syncs it.
"""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.providers.aws.mocked.conftest import _make_logger, _make_moto_aws_client
from tests.providers.aws.mocked.test_cli_onmoto import _make_patched_initialize, _run_orb_cli
from tests.shared.response_helpers import extract_request_id
from tests.shared.scenarios import get_smoke_scenarios

pytestmark = [pytest.mark.moto, pytest.mark.cli]

PENDING_DESCRIBES = 2


def _hold_instances_pending(aws_client, describes: int = PENDING_DESCRIBES) -> None:
    """Report instances as pending for the first ``describes`` DescribeInstances calls."""
    remaining = {"count": describes}

    def _pending(parsed, **_):
        if remaining["count"] <= 0:
            return
        remaining["count"] -= 1
        for reservation in parsed.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instance["State"] = {"Code": 0, "Name": "pending"}

    aws_client.ec2_client.meta.events.register("after-call.ec2.DescribeInstances", _pending)


@pytest.fixture
def initialize(orb_config_dir, moto_aws):
    aws_client = _make_moto_aws_client()
    _hold_instances_pending(aws_client)
    return _make_patched_initialize(aws_client, _make_logger())


def _cli(initialize, args: list[str]) -> dict:
    from orb.bootstrap import Application

    with patch.object(Application, "initialize", initialize):
        return _run_orb_cli(args)


def _machines(initialize) -> list[dict]:
    return _cli(initialize, ["machines", "list"])["machines"]


def test_wait_returns_once_the_machines_are_running(initialize):
    result = _cli(
        initialize,
        [
            "machines",
            "request",
            "--template",
            "RunInstances-OnDemand",
            "--count",
            "1",
            "--wait",
            "--timeout",
            "10",
        ],
    )

    request_id = extract_request_id(result)
    assert request_id, json.dumps(result)
    assert "completed" in result.get("message", ""), json.dumps(result)
    status = _cli(initialize, ["requests", "status", request_id])["requests"][0]
    assert status["status"] == "complete", json.dumps(status)
    machines = _machines(initialize)
    assert machines
    assert all(m["status"] == "running" for m in machines)
    assert all(m["privateIp"] for m in machines)


def test_request_without_wait_returns_immediately_in_progress(initialize):
    started = time.monotonic()
    with patch("asyncio.sleep", side_effect=AssertionError("request must not wait")):
        created = _cli(
            initialize,
            ["machines", "request", "--template", "RunInstances-OnDemand", "--count", "1"],
        )
    assert time.monotonic() - started < 30
    request_id = extract_request_id(created)
    assert request_id

    # The next status read still sees pending instances: the request is in progress.
    first = _cli(initialize, ["requests", "status", request_id])["requests"][0]
    assert first["status"] == "running", json.dumps(first)


@pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
def test_request_without_wait_never_sleeps(initialize, scenario):
    with patch("asyncio.sleep", side_effect=AssertionError("request must not wait")):
        created = _cli(
            initialize,
            ["machines", "request", "--template", scenario.template_id, "--count", "1"],
        )
    assert extract_request_id(created)


def test_hostfactory_request_machines_returns_immediately(initialize):
    payload = json.dumps({"template": {"templateId": "RunInstances-OnDemand", "machineCount": 1}})
    with patch("asyncio.sleep", side_effect=AssertionError("request must not wait")):
        created = _cli(initialize, ["machines", "request", "-d", payload])
    assert created.get("requestId") or created.get("request_id"), json.dumps(created)
