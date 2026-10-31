"""RunInstances renders spot market options from the default spec.

The default spec is the path production uses (the native spec service renders
it); the legacy parameter builder already handled spot and is covered elsewhere.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest

from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.services.aws_native_spec_service import AWSNativeSpecService
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
    config_port.get_package_info.return_value = {"name": "open-resource-broker"}
    native = MagicMock()
    native.spec_renderer = JinjaSpecRenderer(logger)
    native.is_native_spec_enabled.return_value = False
    return RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=AWSOperations(aws_client, logger, config_port),
        launch_template_manager=_make_launch_template_manager(aws_client),
        config_port=config_port,
        aws_native_spec_service=AWSNativeSpecService(native, config_port),
    )


def _acquire(handler: RunInstancesHandler, template: AWSTemplate) -> dict[str, Any]:
    sent: dict[str, Any] = {}
    real = handler.aws_client.ec2_client.run_instances

    def capture(**kwargs: Any) -> Any:
        sent.update(kwargs)
        return real(**kwargs)

    with patch.object(handler.aws_client.ec2_client, "run_instances", side_effect=capture):
        result = handler.acquire_hosts(_make_request(request_id="req-spot-spec"), template)
    assert result["success"] is True
    sent["_instance_ids"] = result["instance_ids"]
    return sent


def _lifecycle(instance_ids: list[str]) -> set[str | None]:
    reservations = boto3.client("ec2", region_name=REGION).describe_instances(
        InstanceIds=instance_ids
    )["Reservations"]
    return {i.get("InstanceLifecycle") for r in reservations for i in r["Instances"]}


def test_spot_template_renders_market_options_with_the_max_price(handler, moto_vpc_resources):
    template = _run_instances_template(
        moto_vpc_resources["subnet_ids"][0],
        moto_vpc_resources["sg_id"],
        price_type="spot",
        max_price=0.05,
    )

    sent = _acquire(handler, template)

    assert sent["InstanceMarketOptions"] == {
        "MarketType": "spot",
        "SpotOptions": {"MaxPrice": "0.05"},
    }
    assert _lifecycle(sent["_instance_ids"]) == {"spot"}
    resource_types = [spec["ResourceType"] for spec in sent["TagSpecifications"]]
    assert resource_types == ["instance", "spot-instances-request"]
    tags = {spec["ResourceType"]: spec["Tags"] for spec in sent["TagSpecifications"]}
    assert tags["instance"] == tags["spot-instances-request"]


def test_spot_template_without_a_max_price_renders_only_the_market_type(
    handler, moto_vpc_resources
):
    template = _run_instances_template(
        moto_vpc_resources["subnet_ids"][0], moto_vpc_resources["sg_id"], price_type="spot"
    )

    sent = _acquire(handler, template)

    assert sent["InstanceMarketOptions"] == {"MarketType": "spot"}
    assert _lifecycle(sent["_instance_ids"]) == {"spot"}


def test_on_demand_template_is_unchanged(handler, moto_vpc_resources):
    template = _run_instances_template(
        moto_vpc_resources["subnet_ids"][0], moto_vpc_resources["sg_id"], price_type="ondemand"
    )

    sent = _acquire(handler, template)

    assert "InstanceMarketOptions" not in sent
    assert [spec["ResourceType"] for spec in sent["TagSpecifications"]] == ["instance"]
    assert _lifecycle(sent["_instance_ids"]) == {None}
