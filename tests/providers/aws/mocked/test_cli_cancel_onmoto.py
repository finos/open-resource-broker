"""Real ``orb requests cancel`` flows against moto-mocked AWS.

moto launches instances for a fleet or group at once, so the status check of the
handlers is replaced with the answer AWS gives for an unfulfilled resource
(in progress, nothing running). Everything else is real: the CLI, the
orchestrators, the provider operation, the handlers and the calls into moto.
"""

import asyncio
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.providers.aws.mocked.conftest import _make_logger, _make_moto_aws_client
from tests.providers.aws.mocked.test_cli_onmoto import _make_patched_initialize
from tests.shared.response_helpers import extract_request_id as _extract_request_id

REGION = "eu-west-2"

pytestmark = [pytest.mark.moto, pytest.mark.cli]


def _run_cli(args: list[str], allow_empty: bool = False) -> tuple[dict, int]:
    """Run the CLI in-process; return its parsed JSON output and exit code."""
    from orb.cli.main import main
    from orb.infrastructure.di.container import reset_container

    original_argv = sys.argv[:]
    original_console = os.environ.get("ORB_LOG_CONSOLE_ENABLED")
    sys.argv = ["orb"] + args
    os.environ["ORB_LOG_CONSOLE_ENABLED"] = "false"
    stdout = io.StringIO()
    exit_code = 0
    try:
        with contextlib.redirect_stdout(stdout):
            try:
                asyncio.run(main())
            except SystemExit as exc:
                exit_code = (
                    exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
                )
    finally:
        sys.argv = original_argv
        if original_console is None:
            os.environ.pop("ORB_LOG_CONSOLE_ENABLED", None)
        else:
            os.environ["ORB_LOG_CONSOLE_ENABLED"] = original_console
        reset_container()

    output = stdout.getvalue().strip()
    if not output and allow_empty:
        return {}, exit_code
    assert output, f"CLI produced no output for args: {args}"
    parsed = json.loads(output)
    if isinstance(parsed, list) and len(parsed) == 2 and isinstance(parsed[0], dict):
        parsed = parsed[0]
    return parsed, exit_code


def _create_request(initialize, template: str) -> str:
    from orb.bootstrap import Application

    with patch.object(Application, "initialize", initialize):
        result, _ = _run_cli(["machines", "request", "--template", template, "--count", "1"])
    request_id = _extract_request_id(result)
    assert request_id, f"No request_id: {result}"
    return request_id


def _status(initialize, request_id: str) -> str:
    from orb.bootstrap import Application

    with patch.object(Application, "initialize", initialize):
        result, _ = _run_cli(["requests", "status", request_id])
    requests = result.get("requests") or [result]
    return str(requests[0].get("status", "")).lower()


@pytest.fixture(autouse=True)
def _unfulfilled_resources():
    from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
    from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
    from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler

    def in_progress(self, request):
        return CheckHostsStatusResult(
            instances=[],
            fulfilment=ProviderFulfilment(
                state="in_progress",
                message="Waiting for capacity",
                target_units=request.requested_count,
                running_count=0,
                pending_count=0,
                failed_count=0,
            ),
        )

    with (
        patch.object(EC2FleetHandler, "check_hosts_status", in_progress),
        patch.object(ASGHandler, "check_hosts_status", in_progress),
    ):
        yield


@pytest.fixture
def initialize(orb_config_dir, moto_aws):
    return _make_patched_initialize(_make_moto_aws_client(), _make_logger())


def test_cancelling_an_unfulfilled_ec2_fleet_request_aborts_the_fleet(initialize, moto_aws):
    from orb.bootstrap import Application

    request_id = _create_request(initialize, "EC2Fleet-Request-OnDemand")
    ec2 = boto3.client("ec2", region_name=REGION)
    fleets = ec2.describe_fleets()["Fleets"]
    assert fleets and all(not f["FleetState"].startswith("deleted") for f in fleets)

    with patch.object(Application, "initialize", initialize):
        cancelled, exit_code = _run_cli(["requests", "cancel", request_id])

    assert exit_code == 0, cancelled
    assert cancelled.get("provider_abort"), cancelled
    assert {o["outcome"] for o in cancelled["provider_abort"]} == {"aborted"}
    fleets = ec2.describe_fleets()["Fleets"]
    assert all(f["FleetState"].startswith("deleted") for f in fleets)
    assert _status(initialize, request_id) in {"cancelled", "complete_with_error"}

    # Cancelling again is a safe no-op that still exits 0.
    with patch.object(Application, "initialize", initialize):
        again, again_exit = _run_cli(["requests", "cancel", request_id])
    assert again_exit == 0, again


def test_cancelling_an_unfulfilled_asg_request_deletes_the_group(initialize, moto_aws):
    from orb.bootstrap import Application

    request_id = _create_request(initialize, "ASG-OnDemand")
    autoscaling = boto3.client("autoscaling", region_name=REGION)
    assert autoscaling.describe_auto_scaling_groups()["AutoScalingGroups"]

    with patch.object(Application, "initialize", initialize):
        cancelled, exit_code = _run_cli(["requests", "cancel", request_id])

    assert exit_code == 0, cancelled
    assert autoscaling.describe_auto_scaling_groups()["AutoScalingGroups"] == []


def test_a_failed_abort_exits_non_zero_and_leaves_the_request_active(initialize, moto_aws):
    from orb.bootstrap import Application

    request_id = _create_request(initialize, "EC2Fleet-Request-OnDemand")
    ec2 = boto3.client("ec2", region_name=REGION)

    def deny(*args, **kwargs):
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "no"}}, "x")

    with patch.object(Application, "initialize", initialize):
        from orb.providers.aws.infrastructure.handlers.ec2_fleet.release_manager import (
            EC2FleetReleaseManager,
        )

        with patch.object(EC2FleetReleaseManager, "_cancel_or_delete_fleet", side_effect=deny):
            failed, exit_code = _run_cli(["requests", "cancel", request_id], allow_empty=True)

    assert exit_code != 0, failed
    assert all(not f["FleetState"].startswith("deleted") for f in ec2.describe_fleets()["Fleets"])
    assert _status(initialize, request_id) not in {"cancelled"}
