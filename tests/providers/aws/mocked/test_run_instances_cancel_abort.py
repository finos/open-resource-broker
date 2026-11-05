"""Aborting a RunInstances launch terminates the instances the launch returned.

DescribeInstances by reservation id is eventually consistent, so a lookup made
right after the launch can come back empty while the instances are running.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import boto3
import pytest

from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.utilities.aws_operations import AWSOperations
from tests.providers.aws.mocked.test_run_instances_gaps import (
    _make_aws_client,
    _make_config_port,
    _make_launch_template_manager,
    _make_logger,
    _make_request,
    _run_instances_template,
)

REGION = "eu-west-2"


@pytest.fixture
def handler(moto_aws):
    aws_client = _make_aws_client()
    logger = _make_logger()
    config_port = _make_config_port()
    return RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=AWSOperations(aws_client, logger, config_port),
        launch_template_manager=_make_launch_template_manager(aws_client),
        config_port=config_port,
    )


def _launch(handler: RunInstancesHandler, moto_vpc_resources: dict[str, Any]) -> dict[str, Any]:
    template = _run_instances_template(
        moto_vpc_resources["subnet_ids"][0], moto_vpc_resources["sg_id"]
    )
    result = handler.acquire_hosts(_make_request(request_id="req-abort"), template)
    assert result["success"] is True
    return result


def _live(instance_ids: list[str]) -> set[str]:
    reservations = boto3.client("ec2", region_name=REGION).describe_instances(
        InstanceIds=instance_ids
    )["Reservations"]
    return {
        i["InstanceId"]
        for r in reservations
        for i in r["Instances"]
        if i["State"]["Name"] in ("pending", "running")
    }


def test_returned_instance_ids_are_terminated_when_the_reservation_lookup_is_empty(
    handler, moto_vpc_resources
):
    result = _launch(handler, moto_vpc_resources)
    reservation_id = result["resource_ids"][0]
    instance_ids = result["instance_ids"]
    assert _live(instance_ids) == set(instance_ids)

    with patch.object(handler, "_lookup_reservation_instance_ids", return_value=[]):
        outcome = handler.cancel_resource_with_members(reservation_id, "req-abort", instance_ids)

    assert outcome["status"] == "success"
    assert _live(instance_ids) == set()


def test_returned_instance_ids_are_terminated_when_the_reservation_lookup_fails(
    handler, moto_vpc_resources
):
    result = _launch(handler, moto_vpc_resources)
    instance_ids = result["instance_ids"]

    with patch.object(
        handler, "_lookup_reservation_instance_ids", side_effect=RuntimeError("describe down")
    ):
        outcome = handler.cancel_resource_with_members(
            result["resource_ids"][0], "req-abort", instance_ids
        )

    assert outcome["status"] == "success"
    assert _live(instance_ids) == set()


def test_without_known_ids_an_empty_lookup_is_retried_before_it_is_given_up(
    handler, moto_vpc_resources
):
    result = _launch(handler, moto_vpc_resources)
    reservation_id = result["resource_ids"][0]
    instance_ids = result["instance_ids"]
    lookups: list[int] = []

    def lagging_lookup(resource_id: str) -> list[str]:
        lookups.append(1)
        # Describe lag: the instances only become visible on the third lookup.
        return list(instance_ids) if len(lookups) >= 3 else []

    with (
        patch.object(handler, "_lookup_reservation_instance_ids", side_effect=lagging_lookup),
        patch("orb.providers.aws.infrastructure.handlers.run_instances.handler.time.sleep"),
    ):
        outcome = handler.cancel_resource(reservation_id, "req-abort")

    assert outcome["status"] == "success"
    assert len(lookups) == 3
    assert _live(instance_ids) == set()


def test_the_lookup_retry_is_bounded(handler):
    with (
        patch.object(handler, "_lookup_reservation_instance_ids", return_value=[]) as lookup,
        patch("orb.providers.aws.infrastructure.handlers.run_instances.handler.time.sleep"),
    ):
        outcome = handler.cancel_resource("r-none", "req-abort")

    assert outcome["status"] == "success"
    assert lookup.call_count == 1 + RunInstancesHandler._CANCEL_LOOKUP_RETRIES


def test_a_failed_lookup_without_known_ids_is_still_an_error(handler):
    with patch.object(
        handler, "_lookup_reservation_instance_ids", side_effect=RuntimeError("describe down")
    ):
        outcome = handler.cancel_resource("r-none", "req-abort")

    assert outcome["status"] == "error"
