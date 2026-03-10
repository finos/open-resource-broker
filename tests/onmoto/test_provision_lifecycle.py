"""Full provision lifecycle integration tests against moto-mocked AWS.

Tests the complete acquire → verify → release pipeline for all four provider
APIs (ASG, EC2Fleet, SpotFleet, RunInstances) using real handler construction
via AWSHandlerFactory.

Moto limitations accounted for:
- ASG: does not auto-spin-up instances (check_hosts_status returns [])
- EC2Fleet instant: returns no instances
- SpotFleet: does not fulfil spot instances
- RunInstances: fully supported — instances are created and visible
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


def _make_config_port(prefix: str = "") -> Any:
    from orb.config.schemas.cleanup_schema import CleanupConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = prefix
    provider_defaults = ProviderDefaults(cleanup=CleanupConfig(enabled=False).model_dump())
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
    request_id: str = "req-integ-001",
    requested_count: int = 1,
    resource_ids: list[str] | None = None,
    provider_data: dict | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-integ"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _make_launch_template_manager(aws_client: AWSClient, logger: Any) -> Any:
    """Build a moto-backed launch template manager (same pattern as aws_mock conftest)."""
    from orb.providers.aws.infrastructure.launch_template.manager import (
        AWSLaunchTemplateManager,
        LaunchTemplateResult,
    )

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
    """Build an AWSHandlerFactory with a moto-backed launch template manager injected."""
    factory = AWSHandlerFactory(aws_client=aws_client, logger=logger, config=config_port)
    # Pre-populate handler cache with real handlers that use the moto LT manager
    from orb.providers.aws.domain.template.value_objects import ProviderApi
    from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
    from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
    from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
    from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
    from orb.providers.aws.utilities.aws_operations import AWSOperations

    lt_manager = _make_launch_template_manager(aws_client, logger)
    aws_ops = AWSOperations(aws_client, logger, config_port)

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
    # Strip instance tag spec that moto rejects
    original_build = spot_handler._config_builder.build

    def _patched_build(**kwargs: Any) -> dict:
        config = original_build(**kwargs)
        tag_specs = config.get("TagSpecifications", [])
        config["TagSpecifications"] = [
            ts for ts in tag_specs if ts.get("ResourceType") != "instance"
        ]
        return config

    spot_handler._config_builder.build = _patched_build
    factory._handlers[ProviderApi.SPOT_FLEET.value] = spot_handler

    return factory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aws_client(moto_aws):
    return _make_aws_client()


@pytest.fixture
def factory(aws_client):
    logger = _make_logger()
    config_port = _make_config_port()
    return _make_factory(aws_client, logger, config_port)


@pytest.fixture
def subnet_id(moto_vpc_resources):
    return moto_vpc_resources["subnet_ids"][0]


@pytest.fixture
def sg_id(moto_vpc_resources):
    return moto_vpc_resources["sg_id"]


# ---------------------------------------------------------------------------
# Template factories per provider API
# ---------------------------------------------------------------------------


def _asg_template(subnet_id: str, sg_id: str) -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-asg",
        name="test-asg",
        provider_api="ASG",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )


def _ec2_fleet_template(subnet_id: str, sg_id: str, fleet_type: str = "instant") -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-fleet",
        name="test-fleet",
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


def _spot_fleet_template(subnet_id: str, sg_id: str, fleet_type: str = "request") -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-spot",
        name="test-spot",
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


def _run_instances_template(subnet_id: str, sg_id: str) -> AWSTemplate:
    return AWSTemplate(
        template_id="tpl-run",
        name="test-run",
        provider_api="RunInstances",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=5,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )


# ---------------------------------------------------------------------------
# ASG lifecycle
# ---------------------------------------------------------------------------


class TestASGProvisionLifecycle:
    def test_acquire_creates_asg(self, factory, subnet_id, sg_id, autoscaling_client):
        """acquire_hosts creates an ASG visible via describe_auto_scaling_groups."""
        handler = factory.create_handler("ASG")
        template = _asg_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-asg-001", requested_count=2)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        asg_name = result["resource_ids"][0]
        resp = autoscaling_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        assert len(resp["AutoScalingGroups"]) == 1
        assert resp["AutoScalingGroups"][0]["DesiredCapacity"] == 2

    def test_check_status_after_acquire(self, factory, subnet_id, sg_id):
        """check_hosts_status returns a list after acquire (moto has no instances yet)."""
        handler = factory.create_handler("ASG")
        template = _asg_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-asg-002", requested_count=1)

        acquire_result = handler.acquire_hosts(request, template)
        asg_name = acquire_result["resource_ids"][0]

        status_request = _make_request(resource_ids=[asg_name])
        result = handler.check_hosts_status(status_request)

        # moto does not spin up ASG instances automatically
        assert isinstance(result, list)

    def test_release_after_acquire(self, factory, subnet_id, sg_id, autoscaling_client):
        """release_hosts with empty machine_ids does not raise after acquire."""
        handler = factory.create_handler("ASG")
        template = _asg_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-asg-003", requested_count=1)

        handler.acquire_hosts(request, template)
        # No real instances in moto — release with empty list is the valid path
        handler.release_hosts([])


# ---------------------------------------------------------------------------
# EC2Fleet lifecycle
# ---------------------------------------------------------------------------


class TestEC2FleetProvisionLifecycle:
    @pytest.mark.parametrize("fleet_type", ["instant", "maintain", "request"])
    def test_acquire_creates_fleet(self, factory, subnet_id, sg_id, ec2_client, fleet_type):
        """acquire_hosts creates an EC2 Fleet visible via describe_fleets."""
        handler = factory.create_handler("EC2Fleet")
        template = _ec2_fleet_template(subnet_id, sg_id, fleet_type=fleet_type)
        request = _make_request(request_id=f"integ-fleet-{fleet_type}", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        fleet_id = result["resource_ids"][0]
        assert fleet_id.startswith("fleet-")

        resp = ec2_client.describe_fleets(FleetIds=[fleet_id])
        assert len(resp["Fleets"]) == 1
        assert resp["Fleets"][0]["FleetId"] == fleet_id

    def test_provider_data_resource_type(self, factory, subnet_id, sg_id):
        """provider_data identifies the resource as ec2_fleet."""
        handler = factory.create_handler("EC2Fleet")
        template = _ec2_fleet_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-fleet-pd", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["provider_data"]["resource_type"] == "ec2_fleet"

    def test_check_status_after_acquire(self, factory, subnet_id, sg_id):
        """check_hosts_status returns a list after acquire (moto instant fleet has no instances)."""
        handler = factory.create_handler("EC2Fleet")
        template = _ec2_fleet_template(subnet_id, sg_id, fleet_type="instant")
        request = _make_request(request_id="integ-fleet-status", requested_count=1)

        acquire_result = handler.acquire_hosts(request, template)
        fleet_id = acquire_result["resource_ids"][0]

        status_request = _make_request(resource_ids=[fleet_id])
        result = handler.check_hosts_status(status_request)

        assert isinstance(result, list)

    def test_release_after_acquire(self, factory, subnet_id, sg_id):
        """release_hosts with empty machine_ids does not raise."""
        handler = factory.create_handler("EC2Fleet")
        template = _ec2_fleet_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-fleet-release", requested_count=1)

        handler.acquire_hosts(request, template)
        handler.release_hosts([])


# ---------------------------------------------------------------------------
# SpotFleet lifecycle
# ---------------------------------------------------------------------------


class TestSpotFleetProvisionLifecycle:
    @pytest.mark.parametrize("fleet_type", ["request", "maintain"])
    def test_acquire_creates_spot_fleet(self, factory, subnet_id, sg_id, ec2_client, fleet_type):
        """acquire_hosts creates a Spot Fleet request visible via describe_spot_fleet_requests."""
        handler = factory.create_handler("SpotFleet")
        template = _spot_fleet_template(subnet_id, sg_id, fleet_type=fleet_type)
        request = _make_request(request_id=f"integ-spot-{fleet_type}", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        fleet_id = result["resource_ids"][0]
        assert fleet_id.startswith("sfr-")

        resp = ec2_client.describe_spot_fleet_requests(SpotFleetRequestIds=[fleet_id])
        assert len(resp["SpotFleetRequestConfigs"]) == 1
        assert resp["SpotFleetRequestConfigs"][0]["SpotFleetRequestId"] == fleet_id

    def test_provider_data_resource_type(self, factory, subnet_id, sg_id):
        """provider_data identifies the resource as spot_fleet."""
        handler = factory.create_handler("SpotFleet")
        template = _spot_fleet_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-spot-pd", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["provider_data"]["resource_type"] == "spot_fleet"

    def test_check_status_after_acquire(self, factory, subnet_id, sg_id):
        """check_hosts_status returns a list after acquire (moto does not fulfil spot)."""
        handler = factory.create_handler("SpotFleet")
        template = _spot_fleet_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-spot-status", requested_count=1)

        acquire_result = handler.acquire_hosts(request, template)
        fleet_id = acquire_result["resource_ids"][0]

        status_request = _make_request(resource_ids=[fleet_id])
        result = handler.check_hosts_status(status_request)

        assert isinstance(result, list)

    def test_release_after_acquire(self, factory, subnet_id, sg_id):
        """release_hosts with empty machine_ids does not raise."""
        handler = factory.create_handler("SpotFleet")
        template = _spot_fleet_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-spot-release", requested_count=1)

        handler.acquire_hosts(request, template)
        handler.release_hosts([])


# ---------------------------------------------------------------------------
# RunInstances lifecycle (moto fully supports this)
# ---------------------------------------------------------------------------


class TestRunInstancesProvisionLifecycle:
    def test_acquire_returns_reservation_id(self, factory, subnet_id, sg_id):
        """acquire_hosts returns a reservation ID."""
        handler = factory.create_handler("RunInstances")
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-run-001", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert len(result["resource_ids"]) == 1
        assert result["resource_ids"][0].startswith("r-")

    def test_acquire_instances_exist_in_aws(self, factory, subnet_id, sg_id, ec2_client):
        """Instances launched by acquire_hosts are visible via describe_instances."""
        handler = factory.create_handler("RunInstances")
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-run-002", requested_count=2)

        result = handler.acquire_hosts(request, template)

        instance_ids = result["provider_data"]["instance_ids"]
        assert len(instance_ids) >= 1

        resp = ec2_client.describe_instances(InstanceIds=instance_ids)
        found = [i["InstanceId"] for r in resp["Reservations"] for i in r["Instances"]]
        assert set(instance_ids) == set(found)

    def test_check_status_returns_instance_data(self, factory, subnet_id, sg_id):
        """check_hosts_status returns one entry per launched instance."""
        handler = factory.create_handler("RunInstances")
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-run-003", requested_count=1)

        acquire_result = handler.acquire_hosts(request, template)
        instance_ids = acquire_result["provider_data"]["instance_ids"]
        reservation_id = acquire_result["resource_ids"][0]

        status_request = _make_request(
            request_id="integ-run-003",
            resource_ids=[reservation_id],
            provider_data={
                "instance_ids": instance_ids,
                "reservation_id": reservation_id,
            },
        )
        result = handler.check_hosts_status(status_request)

        assert len(result) == len(instance_ids)
        assert result[0]["instance_id"] == instance_ids[0]

    def test_release_terminates_instances(self, factory, subnet_id, sg_id, ec2_client):
        """release_hosts terminates the launched instances."""
        handler = factory.create_handler("RunInstances")
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-run-004", requested_count=1)

        acquire_result = handler.acquire_hosts(request, template)
        instance_ids = acquire_result["provider_data"]["instance_ids"]

        handler.release_hosts(instance_ids)

        resp = ec2_client.describe_instances(InstanceIds=instance_ids)
        states = [i["State"]["Name"] for r in resp["Reservations"] for i in r["Instances"]]
        assert all(s in ("shutting-down", "terminated") for s in states)

    def test_full_lifecycle(self, factory, subnet_id, sg_id, ec2_client):
        """Full acquire → status check → release lifecycle for RunInstances."""
        handler = factory.create_handler("RunInstances")
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="integ-run-005", requested_count=1)

        # Acquire
        acquire_result = handler.acquire_hosts(request, template)
        assert acquire_result["success"] is True
        instance_ids = acquire_result["provider_data"]["instance_ids"]
        reservation_id = acquire_result["resource_ids"][0]

        # Verify running
        resp = ec2_client.describe_instances(InstanceIds=instance_ids)
        states = [i["State"]["Name"] for r in resp["Reservations"] for i in r["Instances"]]
        assert all(s in ("pending", "running") for s in states)

        # Status check
        status_request = _make_request(
            request_id="integ-run-005",
            resource_ids=[reservation_id],
            provider_data={"instance_ids": instance_ids, "reservation_id": reservation_id},
        )
        status = handler.check_hosts_status(status_request)
        assert len(status) >= 1

        # Release
        handler.release_hosts(instance_ids)

        resp = ec2_client.describe_instances(InstanceIds=instance_ids)
        states = [i["State"]["Name"] for r in resp["Reservations"] for i in r["Instances"]]
        assert all(s in ("shutting-down", "terminated") for s in states)

    def test_template_defaults_present_in_handler(self, factory, subnet_id, sg_id):
        """Template passed to handler has subnet_ids and security_group_ids populated."""
        handler = factory.create_handler("RunInstances")
        template = _run_instances_template(subnet_id, sg_id)

        assert len(template.subnet_ids) > 0
        assert template.subnet_ids[0] == subnet_id
        assert len(template.security_group_ids) > 0
        assert template.security_group_ids[0] == sg_id

        request = _make_request(request_id="integ-run-006", requested_count=1)
        result = handler.acquire_hosts(request, template)
        assert result["success"] is True
