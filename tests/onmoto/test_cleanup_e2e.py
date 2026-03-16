"""End-to-end cleanup verification tests against moto-mocked AWS.

Verifies the full cleanup chain for every resource type:
  acquire_hosts() -> cancel/release -> assert backing resource gone -> assert LT gone

One test per resource type:
  - ASG
  - EC2Fleet maintain
  - EC2Fleet request
  - EC2Fleet instant
  - SpotFleet maintain
  - SpotFleet request

Moto limitations accounted for:
- SpotFleet: describe_spot_fleet_requests returns empty Tags/TagSpecifications, so
  _maybe_cleanup_launch_template cannot find orb:request-id from fleet tags.
  Workaround: call handler._delete_orb_launch_template(request_id) directly after
  cancel_resource to exercise that path end-to-end.
- EC2Fleet instant: moto does not auto-delete instant fleets (real AWS does).
  Only LT deletion is asserted; fleet state is not checked.
- Mock lt_manager tags every created LT with orb:request-id and orb:managed-by so
  _delete_orb_launch_template can find and verify ownership before deleting.
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
from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from orb.providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
    LaunchTemplateResult,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations

pytestmark = [pytest.mark.moto]

REGION = "eu-west-2"
SPOT_FLEET_ROLE = (
    "arn:aws:iam::123456789012:role/aws-service-role/"
    "spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_cleanup_config_port(prefix: str = "") -> Any:
    """Config port with cleanup fully enabled — distinct from the disabled default."""
    from orb.config.schemas.cleanup_schema import CleanupConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = prefix
    provider_defaults = ProviderDefaults(
        cleanup=CleanupConfig(
            enabled=True,
            delete_launch_template=True,
            dry_run=False,
        ).model_dump()
    )
    provider_config = MagicMock()
    provider_config.provider_defaults = {"aws": provider_defaults}
    config_port.get_provider_config.return_value = provider_config
    return config_port


def _make_aws_client(region: str = REGION) -> AWSClient:
    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=region)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=region)
    aws_client.sts_client = boto3.client("sts", region_name=region)
    return aws_client


def _make_request(
    request_id: str = "req-cleanup-001",
    requested_count: int = 1,
    resource_ids: list[str] | None = None,
    provider_data: dict | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-cleanup"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _make_tagged_lt_manager(aws_client: AWSClient, logger: Any) -> AWSLaunchTemplateManager:
    """Mock LT manager that tags every created LT with orb:request-id and orb:managed-by.

    The tags are required so _delete_orb_launch_template can find the LT via
    describe_launch_templates(Filters=[tag:orb:request-id]) and verify ownership
    via the orb:managed-by tag before deleting.
    """
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
                        else "t3.medium"
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
                TagSpecifications=[
                    {
                        "ResourceType": "launch-template",
                        "Tags": [
                            {"Key": "orb:request-id", "Value": str(request.request_id)},
                            {"Key": "orb:managed-by", "Value": "open-resource-broker"},
                        ],
                    }
                ],
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


def _make_factory(aws_client: AWSClient, logger: Any, config_port: Any) -> AWSHandlerFactory:
    """Build an AWSHandlerFactory with tagged LT manager and SpotFleet moto patch."""
    from orb.providers.aws.domain.template.value_objects import ProviderApi
    from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler

    lt_manager = _make_tagged_lt_manager(aws_client, logger)
    aws_ops = AWSOperations(aws_client, logger, config_port)

    factory = AWSHandlerFactory(aws_client=aws_client, logger=logger, config=config_port)

    factory._handlers[ProviderApi.ASG.value] = ASGHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    factory._handlers[ProviderApi.EC2_FLEET.value] = EC2FleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    factory._handlers[ProviderApi.RUN_INSTANCES.value] = RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )

    spot_handler = SpotFleetHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )
    # Strip instance TagSpecifications that moto rejects, and ensure AllocationStrategy present
    original_build = spot_handler._config_builder.build

    def _patched_spot_build(
        template: AWSTemplate,
        request: Any,
        lt_id: str,
        lt_version: str,
    ) -> dict[str, Any]:
        config = original_build(
            template=template, request=request, lt_id=lt_id, lt_version=lt_version
        )
        tag_specs = config.get("TagSpecifications", [])
        config["TagSpecifications"] = [
            ts for ts in tag_specs if ts.get("ResourceType") != "instance"
        ]
        if "AllocationStrategy" not in config:
            config["AllocationStrategy"] = "lowestPrice"
        return config

    spot_handler._config_builder.build = _patched_spot_build  # type: ignore[method-assign]
    factory._handlers[ProviderApi.SPOT_FLEET.value] = spot_handler

    return factory


def _assert_no_lt_for_request(ec2_client: Any, request_id: str) -> None:
    """Assert that no launch template tagged with orb:request-id=<request_id> exists."""
    resp = ec2_client.describe_launch_templates(
        Filters=[{"Name": "tag:orb:request-id", "Values": [request_id]}]
    )
    templates = resp.get("LaunchTemplates", [])
    assert templates == [], (
        f"Expected 0 launch templates for request {request_id!r}, "
        f"found {len(templates)}: {[t['LaunchTemplateName'] for t in templates]}"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aws_client(moto_aws):
    return _make_aws_client()


@pytest.fixture
def subnet_id(moto_vpc_resources):
    return moto_vpc_resources["subnet_ids"][0]


@pytest.fixture
def sg_id(moto_vpc_resources):
    return moto_vpc_resources["sg_id"]


@pytest.fixture
def ec2(moto_aws):
    return boto3.client("ec2", region_name=REGION)


@pytest.fixture
def autoscaling(moto_aws):
    return boto3.client("autoscaling", region_name=REGION)


# ---------------------------------------------------------------------------
# Template factories
# ---------------------------------------------------------------------------


def _asg_template(subnet_id: str, sg_id: str) -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-asg-cleanup",
        name="test-asg-cleanup",
        provider_api="ASG",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )


def _ec2_fleet_template(subnet_id: str, sg_id: str, fleet_type: str) -> AWSTemplate:
    return AWSTemplate(
        template_id=f"tpl-fleet-{fleet_type}-cleanup",
        name=f"test-fleet-{fleet_type}-cleanup",
        provider_api="EC2Fleet",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        fleet_type=fleet_type,
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )


def _spot_fleet_template(subnet_id: str, sg_id: str, fleet_type: str) -> AWSTemplate:
    return AWSTemplate(
        template_id=f"tpl-spot-{fleet_type}-cleanup",
        name=f"test-spot-{fleet_type}-cleanup",
        provider_api="SpotFleet",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="spot",
        fleet_type=fleet_type,
        fleet_role=SPOT_FLEET_ROLE,
        allocation_strategy="lowest_price",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )


# ---------------------------------------------------------------------------
# ASG cleanup
# ---------------------------------------------------------------------------


def test_asg_cleanup_deletes_asg_and_lt(aws_client, subnet_id, sg_id, autoscaling, ec2):
    """acquire + cancel_resource deletes the ASG and its launch template."""
    req_id = "cleanup-asg-001"
    logger = _make_logger()
    config_port = _make_cleanup_config_port()
    factory = _make_factory(aws_client, logger, config_port)

    handler = factory.create_handler("ASG")
    template = _asg_template(subnet_id, sg_id)
    request = _make_request(request_id=req_id, requested_count=1)

    # Acquire
    result: dict = handler.acquire_hosts(request, template)  # type: ignore[assignment]
    assert result["success"] is True
    asg_name = result["resource_ids"][0]

    # Verify ASG exists
    resp = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    assert len(resp["AutoScalingGroups"]) == 1

    # Verify LT exists
    lt_resp = ec2.describe_launch_templates(
        Filters=[{"Name": "tag:orb:request-id", "Values": [req_id]}]
    )
    assert len(lt_resp["LaunchTemplates"]) == 1

    # Cancel (deletes ASG + LT)
    cancel_result = handler.cancel_resource(asg_name, req_id)
    assert cancel_result["status"] == "success"

    # Assert ASG gone
    resp = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    assert resp["AutoScalingGroups"] == [], f"ASG {asg_name!r} still exists after cancel_resource"

    # Assert LT gone
    _assert_no_lt_for_request(ec2, req_id)


# ---------------------------------------------------------------------------
# EC2Fleet maintain cleanup
# ---------------------------------------------------------------------------


def test_ec2_fleet_maintain_cleanup_deletes_fleet_and_lt(aws_client, subnet_id, sg_id, ec2):
    """acquire + cancel_resource deletes a maintain-type EC2 Fleet and its LT."""
    req_id = "cleanup-fleet-maintain-001"
    logger = _make_logger()
    config_port = _make_cleanup_config_port()
    factory = _make_factory(aws_client, logger, config_port)

    handler = factory.create_handler("EC2Fleet")
    template = _ec2_fleet_template(subnet_id, sg_id, fleet_type="maintain")
    request = _make_request(request_id=req_id, requested_count=1)

    # Acquire
    result: dict = handler.acquire_hosts(request, template)  # type: ignore[assignment]
    assert result["success"] is True
    fleet_id = result["resource_ids"][0]

    # Verify fleet exists and is active
    resp = ec2.describe_fleets(FleetIds=[fleet_id])
    assert len(resp["Fleets"]) == 1
    assert resp["Fleets"][0]["FleetState"] in ("active", "modifying", "submitted")

    # Verify LT exists
    lt_resp = ec2.describe_launch_templates(
        Filters=[{"Name": "tag:orb:request-id", "Values": [req_id]}]
    )
    assert len(lt_resp["LaunchTemplates"]) == 1

    # Cancel (deletes fleet + LT via _maybe_cleanup_launch_template reading fleet Tags)
    cancel_result = handler.cancel_resource(fleet_id, req_id)
    assert cancel_result["status"] == "success"

    # Assert fleet deleted
    resp = ec2.describe_fleets(FleetIds=[fleet_id])
    fleet_states = [f["FleetState"] for f in resp["Fleets"]]
    assert all(s in ("deleted", "deleted_running", "deleted_terminating") for s in fleet_states), (
        f"Fleet {fleet_id!r} not in deleted state after cancel_resource; states: {fleet_states}"
    )

    # Assert LT gone
    _assert_no_lt_for_request(ec2, req_id)


# ---------------------------------------------------------------------------
# EC2Fleet request cleanup
# ---------------------------------------------------------------------------


def test_ec2_fleet_request_cleanup_deletes_fleet_and_lt(aws_client, subnet_id, sg_id, ec2):
    """acquire + cancel_resource deletes a request-type EC2 Fleet and its LT."""
    req_id = "cleanup-fleet-request-001"
    logger = _make_logger()
    config_port = _make_cleanup_config_port()
    factory = _make_factory(aws_client, logger, config_port)

    handler = factory.create_handler("EC2Fleet")
    template = _ec2_fleet_template(subnet_id, sg_id, fleet_type="request")
    request = _make_request(request_id=req_id, requested_count=1)

    # Acquire
    result: dict = handler.acquire_hosts(request, template)  # type: ignore[assignment]
    assert result["success"] is True
    fleet_id = result["resource_ids"][0]

    # Verify fleet exists
    resp = ec2.describe_fleets(FleetIds=[fleet_id])
    assert len(resp["Fleets"]) == 1

    # Verify LT exists
    lt_resp = ec2.describe_launch_templates(
        Filters=[{"Name": "tag:orb:request-id", "Values": [req_id]}]
    )
    assert len(lt_resp["LaunchTemplates"]) == 1

    # Cancel
    cancel_result = handler.cancel_resource(fleet_id, req_id)
    assert cancel_result["status"] == "success"

    # Assert fleet deleted
    resp = ec2.describe_fleets(FleetIds=[fleet_id])
    fleet_states = [f["FleetState"] for f in resp["Fleets"]]
    assert all(s in ("deleted", "deleted_running", "deleted_terminating") for s in fleet_states), (
        f"Fleet {fleet_id!r} not in deleted state; states: {fleet_states}"
    )

    # Assert LT gone
    _assert_no_lt_for_request(ec2, req_id)


# ---------------------------------------------------------------------------
# EC2Fleet instant cleanup
# ---------------------------------------------------------------------------


def test_ec2_fleet_instant_cleanup_deletes_lt(aws_client, subnet_id, sg_id, ec2):
    """acquire + cancel_resource for an instant fleet deletes the LT.

    Moto does not auto-delete instant fleets (real AWS does), so fleet state
    is not asserted — only LT deletion is verified.
    """
    req_id = "cleanup-fleet-instant-001"
    logger = _make_logger()
    config_port = _make_cleanup_config_port()
    factory = _make_factory(aws_client, logger, config_port)

    handler = factory.create_handler("EC2Fleet")
    template = _ec2_fleet_template(subnet_id, sg_id, fleet_type="instant")
    request = _make_request(request_id=req_id, requested_count=1)

    # Acquire
    result: dict = handler.acquire_hosts(request, template)  # type: ignore[assignment]
    assert result["success"] is True
    fleet_id = result["resource_ids"][0]

    # Verify LT exists
    lt_resp = ec2.describe_launch_templates(
        Filters=[{"Name": "tag:orb:request-id", "Values": [req_id]}]
    )
    assert len(lt_resp["LaunchTemplates"]) == 1

    # Cancel — for instant fleets the release_manager skips fleet deletion and
    # only cleans up the LT (fleet_details will be empty dict from cancel_resource)
    cancel_result = handler.cancel_resource(fleet_id, req_id)
    assert cancel_result["status"] == "success"

    # Assert LT gone (primary assertion for instant fleet cleanup)
    _assert_no_lt_for_request(ec2, req_id)


# ---------------------------------------------------------------------------
# SpotFleet maintain cleanup
# ---------------------------------------------------------------------------


def test_spot_fleet_maintain_cleanup_cancels_fleet_and_deletes_lt(
    aws_client, subnet_id, sg_id, ec2
):
    """acquire + cancel_resource + _delete_orb_launch_template cancels a maintain
    SpotFleet and deletes its LT.

    Moto does not populate Tags on describe_spot_fleet_requests responses, so
    _maybe_cleanup_launch_template cannot find orb:request-id from fleet tags.
    We call handler._delete_orb_launch_template(req_id) directly after
    cancel_resource to exercise the full LT cleanup path end-to-end.
    """
    req_id = "cleanup-spot-maintain-001"
    logger = _make_logger()
    config_port = _make_cleanup_config_port()
    factory = _make_factory(aws_client, logger, config_port)

    handler = factory.create_handler("SpotFleet")
    template = _spot_fleet_template(subnet_id, sg_id, fleet_type="maintain")
    request = _make_request(request_id=req_id, requested_count=1)

    # Acquire
    result: dict = handler.acquire_hosts(request, template)  # type: ignore[assignment]
    assert result["success"] is True
    fleet_id = result["resource_ids"][0]
    assert fleet_id.startswith("sfr-")

    # Verify fleet exists
    resp = ec2.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])
    assert len(resp["SpotFleetRequestConfigs"]) == 1

    # Verify LT exists
    lt_resp = ec2.describe_launch_templates(
        Filters=[{"Name": "tag:orb:request-id", "Values": [req_id]}]
    )
    assert len(lt_resp["LaunchTemplates"]) == 1

    # Cancel fleet (moto tag-reading gap means LT cleanup is skipped inside cancel_resource)
    cancel_result = handler.cancel_resource(fleet_id, req_id)
    assert cancel_result["status"] == "success"

    # Assert fleet cancelled
    resp = ec2.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])
    fleet_states = [c["SpotFleetRequestState"] for c in resp["SpotFleetRequestConfigs"]]
    assert all(
        s in ("cancelled", "cancelled_running", "cancelled_terminating") for s in fleet_states
    ), f"SpotFleet {fleet_id!r} not cancelled; states: {fleet_states}"

    # Directly exercise the LT cleanup path (workaround for moto tag gap)
    handler._delete_orb_launch_template(req_id)

    # Assert LT gone
    _assert_no_lt_for_request(ec2, req_id)


# ---------------------------------------------------------------------------
# SpotFleet request cleanup
# ---------------------------------------------------------------------------


def test_spot_fleet_request_cleanup_cancels_fleet_and_deletes_lt(aws_client, subnet_id, sg_id, ec2):
    """acquire + cancel_resource + _delete_orb_launch_template cancels a request
    SpotFleet and deletes its LT.

    Same moto tag-reading workaround as the maintain variant.
    """
    req_id = "cleanup-spot-request-001"
    logger = _make_logger()
    config_port = _make_cleanup_config_port()
    factory = _make_factory(aws_client, logger, config_port)

    handler = factory.create_handler("SpotFleet")
    template = _spot_fleet_template(subnet_id, sg_id, fleet_type="request")
    request = _make_request(request_id=req_id, requested_count=1)

    # Acquire
    result: dict = handler.acquire_hosts(request, template)  # type: ignore[assignment]
    assert result["success"] is True
    fleet_id = result["resource_ids"][0]
    assert fleet_id.startswith("sfr-")

    # Verify fleet exists
    resp = ec2.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])
    assert len(resp["SpotFleetRequestConfigs"]) == 1

    # Verify LT exists
    lt_resp = ec2.describe_launch_templates(
        Filters=[{"Name": "tag:orb:request-id", "Values": [req_id]}]
    )
    assert len(lt_resp["LaunchTemplates"]) == 1

    # Cancel fleet
    cancel_result = handler.cancel_resource(fleet_id, req_id)
    assert cancel_result["status"] == "success"

    # Assert fleet cancelled
    resp = ec2.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])
    fleet_states = [c["SpotFleetRequestState"] for c in resp["SpotFleetRequestConfigs"]]
    assert all(
        s in ("cancelled", "cancelled_running", "cancelled_terminating") for s in fleet_states
    ), f"SpotFleet {fleet_id!r} not cancelled; states: {fleet_states}"

    # Directly exercise the LT cleanup path (workaround for moto tag gap)
    handler._delete_orb_launch_template(req_id)

    # Assert LT gone
    _assert_no_lt_for_request(ec2, req_id)
