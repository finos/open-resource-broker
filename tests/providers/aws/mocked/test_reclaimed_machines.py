"""HostFactory getReturnRequests lists machines the provider reclaimed.

The machines sit under completed acquire requests, which the status polls no
longer sync, so the answer has to come from a fresh provider read.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.providers.aws.mocked.conftest import _make_logger, _make_moto_aws_client
from tests.providers.aws.mocked.test_cli_onmoto import (
    _extract_machine_ids,
    _extract_request_id,
    _make_patched_initialize,
    _run_orb_cli,
)

REGION = "eu-west-2"
TEMPLATE_ID = "RunInstances-OnDemand"

pytestmark = [pytest.mark.moto, pytest.mark.cli]


def _launch(patched) -> str:
    from orb.bootstrap import Application

    with patch.object(Application, "initialize", patched):
        created = _run_orb_cli(["machines", "request", "--template", TEMPLATE_ID, "--count", "1"])
    request_id = _extract_request_id(created)
    with patch.object(Application, "initialize", patched):
        status = _run_orb_cli(["requests", "status", request_id])
    (machine_id,) = _extract_machine_ids(status)
    return machine_id


def _status_checks(orb_config_dir: Path) -> dict:
    db = json.loads((orb_config_dir.parent / "data" / "request_database.json").read_text())
    return {k: r.get("last_status_check") for k, r in db["requests"].items()}


def _poll(patched, machines: list[dict]) -> list[dict]:
    from orb.bootstrap import Application

    with patch.object(Application, "initialize", patched):
        result = _run_orb_cli(
            ["requests", "list-returns", "-d", json.dumps({"machines": machines})]
        )
    assert result["status"] == "complete", result
    return result["requests"]


def test_poll_lists_only_machines_the_provider_reclaimed(orb_config_dir, moto_aws):
    from orb.bootstrap import Application

    patched = _make_patched_initialize(_make_moto_aws_client(), _make_logger())
    running, reclaimed, stopped = _launch(patched), _launch(patched), _launch(patched)
    ec2 = boto3.client("ec2", region_name=REGION)
    ec2.terminate_instances(InstanceIds=[reclaimed])
    ec2.stop_instances(InstanceIds=[stopped])

    checks_before = _status_checks(orb_config_dir)

    polled = _poll(
        patched,
        [{"machineId": running}, {"machineId": reclaimed}, {"machineId": stopped}],
    )

    db = json.loads((orb_config_dir.parent / "data" / "request_database.json").read_text())
    names = {m["machine_id"]: m["name"] for m in db["machines"].values()}
    assert [item["machine"] for item in polled] == [names[reclaimed]]
    assert isinstance(polled[0]["gracePeriod"], int)

    # The refreshed state is persisted, so the machine is now stored as ended.
    db = json.loads((orb_config_dir.parent / "data" / "request_database.json").read_text())
    stored = {m["machine_id"]: m for m in db["machines"].values()}
    assert stored[reclaimed]["status"] in ("terminated", "shutting-down")
    assert stored[running]["status"] == "running"
    assert stored[stopped]["status"] == "stopped"
    # The parent requests are finished: the poll leaves their freshness stamps alone.
    assert _status_checks(orb_config_dir) == checks_before

    # Once HostFactory returns the machine through ORB it is no longer reported.
    with patch.object(Application, "initialize", patched):
        _run_orb_cli(["machines", "return", reclaimed])
    assert _poll(patched, [{"machineId": reclaimed}]) == []


def test_poll_matches_a_machine_by_name(orb_config_dir, moto_aws):
    patched = _make_patched_initialize(_make_moto_aws_client(), _make_logger())
    machine_id = _launch(patched)
    db = json.loads((orb_config_dir.parent / "data" / "request_database.json").read_text())
    name = next(m["name"] for m in db["machines"].values() if m["machine_id"] == machine_id)
    boto3.client("ec2", region_name=REGION).terminate_instances(InstanceIds=[machine_id])

    polled = _poll(patched, [{"name": name}])

    assert [item["machine"] for item in polled] == [name]


def test_poll_without_machines_is_empty(orb_config_dir, moto_aws):
    patched = _make_patched_initialize(_make_moto_aws_client(), _make_logger())
    assert _poll(patched, []) == []


def test_script_poll_with_empty_input_is_recognised(orb_config_dir, moto_aws, monkeypatch):
    from orb.bootstrap import Application

    patched = _make_patched_initialize(_make_moto_aws_client(), _make_logger())
    monkeypatch.setenv("HF_CALLER_SCRIPT", "getReturnRequests.sh")
    with patch.object(Application, "initialize", patched):
        result = _run_orb_cli(["requests", "list-returns", "-d", "{}"])

    assert result == {"status": "complete", "message": "No machines to return.", "requests": []}
