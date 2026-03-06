"""Partial return tests for RunInstances.

Validates that returning a subset of acquired instances terminates only the
specified instances and leaves the remainder running. RunInstances is used
because moto fully supports instance lifecycle (launch, describe, terminate).
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
    LaunchTemplateResult,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_config_port() -> Any:
    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = ""
    config_port.get_cleanup_config.return_value = {"enabled": False}
    return config_port


def _make_aws_client() -> AWSClient:
    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=REGION)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=REGION)
    aws_client.sts_client = boto3.client("sts", region_name=REGION)
    return aws_client


def _make_launch_template_manager(aws_client: AWSClient) -> AWSLaunchTemplateManager:
    lt_manager = MagicMock(spec=AWSLaunchTemplateManager)

    def _create_or_update(template: AWSTemplate, request: Any) -> LaunchTemplateResult:
        lt_name = f"orb-lt-{request.request_id}"
        try:
            resp = aws_client.ec2_client.create_launch_template(
                LaunchTemplateName=lt_name,
                LaunchTemplateData={
                    "ImageId": template.image_id or "ami-12345678",
                    "InstanceType": (
                        next(iter(template.machine_types.keys()))
                        if template.machine_types
                        else "t3.micro"
                    ),
                    "NetworkInterfaces": [
                        {
                            "DeviceIndex": 0,
                            "SubnetId": template.subnet_ids[0] if template.subnet_ids else "",
                            "Groups": template.security_group_ids or [],
                            "AssociatePublicIpAddress": False,
                        }
                    ],
                },
            )
            lt_id = resp["LaunchTemplate"]["LaunchTemplateId"]
            version = str(resp["LaunchTemplate"]["LatestVersionNumber"])
        except Exception:
            lt_id = "lt-mock"
            version = "1"
        return LaunchTemplateResult(
            template_id=lt_id,
            version=version,
            template_name=lt_name,
            is_new_template=True,
        )

    lt_manager.create_or_update_launch_template.side_effect = _create_or_update
    return lt_manager


def _make_request(
    request_id: str = "req-partial-001",
    requested_count: int = 3,
    resource_ids: list[str] | None = None,
    provider_data: dict | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-partial"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _run_instances_template(subnet_id: str, sg_id: str) -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-partial",
        name="test-partial-return",
        provider_api="RunInstances",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=10,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )


def _instance_states(ec2_client: Any, instance_ids: list[str]) -> list[str]:
    resp = ec2_client.describe_instances(InstanceIds=instance_ids)
    return [i["State"]["Name"] for r in resp["Reservations"] for i in r["Instances"]]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def run_instances_handler(moto_vpc_resources):
    """Construct a RunInstancesHandler directly against moto — no DI stack."""
    aws_client = _make_aws_client()
    logger = _make_logger()
    config_port = _make_config_port()
    lt_manager = _make_launch_template_manager(aws_client)
    aws_ops = AWSOperations(aws_client, logger, config_port)
    return RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


@pytest.fixture
def acquired_3(run_instances_handler, moto_vpc_resources):
    """Acquire 3 instances and return their IDs together with the handler.

    Moto's run_instances always returns exactly 1 instance regardless of MaxCount,
    so we call acquire_hosts three times with requested_count=1 to obtain 3 instances.
    """
    subnet_id = moto_vpc_resources["subnet_ids"][0]
    sg_id = moto_vpc_resources["sg_id"]
    template = _run_instances_template(subnet_id, sg_id)

    instance_ids = []
    reservation_id = None
    for i in range(3):
        request = _make_request(request_id=f"req-partial-acquire-{i}", requested_count=1)
        result = run_instances_handler.acquire_hosts(request, template)
        assert result["success"] is True, f"acquire_hosts failed on call {i}: {result}"
        instance_ids.extend(result["provider_data"]["instance_ids"])
        if reservation_id is None:
            reservation_id = result["resource_ids"][0]

    assert len(instance_ids) == 3, f"Expected 3 instances, got {len(instance_ids)}"

    return {
        "instance_ids": instance_ids,
        "reservation_id": reservation_id,
        "handler": run_instances_handler,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPartialReturnRunInstances:
    def test_return_one_of_three_terminates_only_that_instance(self, acquired_3, ec2_client):
        """Returning one instance terminates it while the other two remain running."""
        instance_ids = acquired_3["instance_ids"]
        handler = acquired_3["handler"]

        handler.release_hosts([instance_ids[0]])

        terminated_states = _instance_states(ec2_client, [instance_ids[0]])
        assert all(s in ("shutting-down", "terminated") for s in terminated_states)

        remaining_states = _instance_states(ec2_client, instance_ids[1:])
        assert all(s in ("pending", "running") for s in remaining_states)

    def test_return_two_of_three_leaves_one_running(self, acquired_3, ec2_client):
        """Returning two instances leaves exactly one still running."""
        instance_ids = acquired_3["instance_ids"]
        handler = acquired_3["handler"]

        handler.release_hosts(instance_ids[:2])

        terminated_states = _instance_states(ec2_client, instance_ids[:2])
        assert all(s in ("shutting-down", "terminated") for s in terminated_states)

        surviving_states = _instance_states(ec2_client, [instance_ids[2]])
        assert all(s in ("pending", "running") for s in surviving_states)

    def test_partial_return_does_not_affect_remaining_instances(self, acquired_3, ec2_client):
        """Instances not in the return list are untouched after a partial return."""
        instance_ids = acquired_3["instance_ids"]
        handler = acquired_3["handler"]

        # Capture state of the two instances we will NOT return before the call
        before_states = _instance_states(ec2_client, instance_ids[1:])
        assert all(s in ("pending", "running") for s in before_states)

        handler.release_hosts([instance_ids[0]])

        after_states = _instance_states(ec2_client, instance_ids[1:])
        assert all(s in ("pending", "running") for s in after_states)

    def test_check_status_after_partial_return_reflects_mixed_states(self, acquired_3):
        """check_hosts_status after a partial return shows both terminated and running entries.

        Because moto returns one instance per acquire_hosts call, we have three separate
        reservation IDs. We use provider_data.instance_ids (the direct lookup path) so
        check_hosts_status can find all three instances regardless of reservation grouping.
        """
        instance_ids = acquired_3["instance_ids"]
        reservation_id = acquired_3["reservation_id"]
        handler = acquired_3["handler"]

        handler.release_hosts([instance_ids[0]])

        # Pass all instance_ids directly so the handler uses the fast describe path
        # rather than the reservation-id filter path (which only covers one reservation).
        status_request = _make_request(
            request_id="req-partial-acquire-0",
            resource_ids=[reservation_id],
            provider_data={
                "instance_ids": instance_ids,
                "reservation_id": reservation_id,
            },
        )
        result = handler.check_hosts_status(status_request)

        assert len(result) == 3

        statuses = {entry["instance_id"]: entry["status"] for entry in result}
        terminated_entry = statuses[instance_ids[0]]
        assert terminated_entry in ("terminated", "shutting-down", "stopping")

        running_entries = [statuses[iid] for iid in instance_ids[1:]]
        assert all(s in ("pending", "running") for s in running_entries)

    def test_return_empty_list_is_noop(self, acquired_3, ec2_client):
        """Calling release_hosts with an empty list does not raise and leaves all instances running."""
        instance_ids = acquired_3["instance_ids"]
        handler = acquired_3["handler"]

        handler.release_hosts([])

        states = _instance_states(ec2_client, instance_ids)
        assert all(s in ("pending", "running") for s in states)

    def test_return_all_terminates_all(self, acquired_3, ec2_client):
        """Returning all three instances terminates every one of them."""
        instance_ids = acquired_3["instance_ids"]
        handler = acquired_3["handler"]

        handler.release_hosts(instance_ids)

        states = _instance_states(ec2_client, instance_ids)
        assert all(s in ("shutting-down", "terminated") for s in states)
