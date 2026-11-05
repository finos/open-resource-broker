"""Returning machines whose EC2 instance no longer exists.

EC2 answers TerminateInstances with InvalidInstanceID.NotFound for an unknown
id and terminates none of the others in the same call. The return must still
complete, terminate the instances that exist and treat the missing ones as
already terminated.
"""

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


def _forget_instance(instance_id: str) -> None:
    """Remove an instance from moto so EC2 reports its id as unknown."""
    from moto.core import DEFAULT_ACCOUNT_ID  # type: ignore[attr-defined]
    from moto.ec2.models import ec2_backends

    backend = ec2_backends[DEFAULT_ACCOUNT_ID][REGION]
    for reservation in backend.reservations.values():
        reservation.instances = [i for i in reservation.instances if i.id != instance_id]


def _states(ec2_client, instance_ids: list[str]) -> dict[str, str]:
    response = ec2_client.describe_instances(InstanceIds=instance_ids)
    return {
        i["InstanceId"]: i["State"]["Name"]
        for r in response["Reservations"]
        for i in r["Instances"]
    }


def _return_status(patched, request_id: str) -> str:
    from unittest.mock import patch

    from orb.bootstrap import Application

    with patch.object(Application, "initialize", patched):
        result = _run_orb_cli(["requests", "status", request_id])
    requests = result.get("requests") or [result]
    return str(requests[0].get("status"))


def _request_machines(patched, count: int) -> list[str]:
    from unittest.mock import patch

    from orb.bootstrap import Application

    with patch.object(Application, "initialize", patched):
        created = _run_orb_cli(
            ["machines", "request", "--template", TEMPLATE_ID, "--count", str(count)]
        )
    request_id = _extract_request_id(created)
    assert request_id
    with patch.object(Application, "initialize", patched):
        status = _run_orb_cli(["requests", "status", request_id])
    machine_ids = _extract_machine_ids(status)
    assert len(machine_ids) == count
    return machine_ids


class TestReturnWithMissingInstances:
    def test_return_completes_when_one_instance_is_gone(self, orb_config_dir, moto_aws):
        from unittest.mock import patch

        from orb.bootstrap import Application

        patched = _make_patched_initialize(_make_moto_aws_client(), _make_logger())
        machine_ids = _request_machines(patched, 1) + _request_machines(patched, 1)
        missing, present = machine_ids
        _forget_instance(missing)

        with patch.object(Application, "initialize", patched):
            returned = _run_orb_cli(["machines", "return", *machine_ids])

        return_request_id = returned.get("request_id") or returned.get("requestId")
        assert return_request_id
        assert return_status_is_complete(patched, return_request_id)

        ec2 = boto3.client("ec2", region_name=REGION)
        assert _states(ec2, [present])[present] in ("shutting-down", "terminated")

        with patch.object(Application, "initialize", patched):
            machines = _run_orb_cli(["machines", "list"])
        states = {
            m.get("machine_id") or m.get("machineId"): m.get("status") for m in _machines(machines)
        }
        assert states[missing] == "terminated"
        assert states[present] == "terminated"

    def test_return_completes_when_every_instance_is_gone(self, orb_config_dir, moto_aws):
        from unittest.mock import patch

        from orb.bootstrap import Application

        patched = _make_patched_initialize(_make_moto_aws_client(), _make_logger())
        (machine_id,) = _request_machines(patched, 1)
        _forget_instance(machine_id)

        with patch.object(Application, "initialize", patched):
            returned = _run_orb_cli(["machines", "return", machine_id])

        return_request_id = returned.get("request_id") or returned.get("requestId")
        assert return_request_id
        assert return_status_is_complete(patched, return_request_id)


def _machines(listing: dict) -> list[dict]:
    return listing.get("machines") or []


def return_status_is_complete(patched, request_id: str) -> bool:
    return _return_status(patched, request_id) == "complete"
