"""Per-request template overrides end to end against moto-mocked AWS.

An SDK request carries overrides, the provisioned template is recorded on the
request, and the machines can still be returned from a fresh process after the
stored template is gone. Disallowed overrides fail before anything is persisted.
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.providers.aws.mocked.conftest import (
    _inject_moto_factory,
    _make_logger,
    _make_moto_aws_client,
)
from tests.providers.aws.mocked.test_cli_onmoto import _make_patched_initialize, _run_orb_cli
from tests.shared.response_helpers import (
    extract_machine_ids as _extract_machine_ids,
    extract_request_id as _extract_request_id,
    extract_status as _extract_status,
)

pytestmark = [pytest.mark.moto, pytest.mark.sdk]

TEMPLATE_ID = "RunInstances-OnDemand"


async def _sdk_request(config_data: dict, overrides: dict | None):
    from orb.sdk.client import ORBClient

    async with ORBClient(app_config=config_data) as sdk:
        _inject_moto_factory(_make_moto_aws_client(), _make_logger(), None)
        created = await sdk.request_machines(TEMPLATE_ID, 1, overrides=overrides)
        request_id = _extract_request_id(created)
        status = await sdk.get_request_status(request_id=request_id, verbose=True)
    return request_id, status


def _request_records(config_dir: Path) -> list[dict]:
    db = next((config_dir.parent / "data").glob("*.json"), None)
    if db is None:
        return []
    data = json.loads(db.read_text())
    requests = data.get("requests", {}) if isinstance(data, dict) else {}
    return list(requests.values()) if isinstance(requests, dict) else list(requests)


def test_override_request_is_returnable_from_a_fresh_process_without_the_template(
    orb_config_dir, moto_aws, moto_vpc_resources
):
    from orb.bootstrap import Application

    config_data = json.loads((orb_config_dir / "config.json").read_text())
    override_subnet = moto_vpc_resources["subnet_ids"][1]

    request_id, status = asyncio.run(_sdk_request(config_data, {"subnet_ids": [override_subnet]}))

    # The status output does not expose the recorded template.
    assert "template_snapshot" not in json.dumps(status, default=str)

    (record,) = _request_records(orb_config_dir)
    metadata = record["metadata"]
    assert metadata["template_overrides"] == {"subnet_ids": [override_subnet]}
    assert metadata["template_snapshot"]["subnet_ids"] == [override_subnet]
    assert "instance_profile" not in metadata["template_snapshot"]

    # The stored template disappears: only the snapshot on the request remains.
    templates_file = orb_config_dir / "aws_templates.json"
    templates_file.write_text(json.dumps({"scheduler_type": "hostfactory", "templates": []}))

    patched = _make_patched_initialize(_make_moto_aws_client(), _make_logger())
    with patch.object(Application, "initialize", patched):
        # Fresh process: the CLI helper resets the DI container on every call.
        status_result = _run_orb_cli(["requests", "status", request_id])
        machine_ids = _extract_machine_ids(status_result)
        assert machine_ids, f"no machines to return: {status_result}"
        result = _run_orb_cli(["machines", "return", *machine_ids])

    return_request_id = result.get("requestId") or result.get("request_id")
    assert return_request_id, result

    deadline = time.time() + 15
    final = None
    with patch.object(Application, "initialize", patched):
        while time.time() < deadline:
            final = _extract_status(_run_orb_cli(["requests", "status", return_request_id]))
            if final in {"complete", "complete_with_error", "failed", "cancelled"}:
                break
            time.sleep(0.5)
    assert final == "complete", final

    ec2 = boto3.client("ec2", region_name="eu-west-2")
    states = {
        i["State"]["Name"]
        for r in ec2.describe_instances(InstanceIds=machine_ids)["Reservations"]
        for i in r["Instances"]
    }
    assert states <= {"shutting-down", "terminated"}, states


@pytest.mark.parametrize("key", ["instance_profile", "machine_role", "not_a_field"])
def test_disallowed_override_fails_before_a_request_is_persisted(
    orb_config_dir, moto_aws, moto_vpc_resources, key
):
    config_data = json.loads((orb_config_dir / "config.json").read_text())

    with pytest.raises(Exception, match=r"(?i)override"):
        asyncio.run(_sdk_request(config_data, {key: "x"}))

    assert _request_records(orb_config_dir) == []
