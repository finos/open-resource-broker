"""Returning a machine that is already terminated and was never stamped.

A machine whose instance ended outside of ORB (for example a spot interruption)
carries no return request id. Returning it must succeed, record the return on
the machine, and leave its terminal state untouched.
"""

import json
import sys
from pathlib import Path

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


def _stored_machines(orb_config_dir: Path) -> dict[str, dict]:
    db = json.loads((orb_config_dir.parent / "data" / "request_database.json").read_text())
    return db["machines"]


def _mark_terminated_in_storage(orb_config_dir: Path, machine_id: str) -> None:
    path = orb_config_dir.parent / "data" / "request_database.json"
    db = json.loads(path.read_text())
    for key, machine in db["machines"].items():
        if machine.get("machine_id") == machine_id or key == machine_id:
            machine["status"] = "terminated"
            machine["return_request_id"] = None
    path.write_text(json.dumps(db))


@pytest.mark.parametrize("identifier", ["machine_id", "name"])
def test_return_of_terminated_unstamped_machine(orb_config_dir, moto_aws, identifier):
    from unittest.mock import patch

    from orb.bootstrap import Application

    patched = _make_patched_initialize(_make_moto_aws_client(), _make_logger())
    with patch.object(Application, "initialize", patched):
        created = _run_orb_cli(["machines", "request", "--template", TEMPLATE_ID, "--count", "1"])
    request_id = _extract_request_id(created)
    with patch.object(Application, "initialize", patched):
        status = _run_orb_cli(["requests", "status", request_id])
    (machine_id,) = _extract_machine_ids(status)

    boto3.client("ec2", region_name=REGION).terminate_instances(InstanceIds=[machine_id])
    _mark_terminated_in_storage(orb_config_dir, machine_id)
    stored = next(
        m for k, m in _stored_machines(orb_config_dir).items() if m.get("machine_id") == machine_id
    )
    target = stored[identifier] if identifier in stored else machine_id

    with patch.object(Application, "initialize", patched):
        returned = _run_orb_cli(["machines", "return", target])

    return_request_id = returned.get("request_id") or returned.get("requestId")
    assert return_request_id, returned
    after = next(
        m for k, m in _stored_machines(orb_config_dir).items() if m.get("machine_id") == machine_id
    )
    assert after["return_request_id"] == return_request_id
    assert after["status"] == "terminated"
