"""ASG price type and lifecycle integration tests against moto-mocked AWS.

Tests cover:
- spot / heterogeneous MixedInstancesPolicy construction
- release with instance attachment (moto does not auto-spin ASG instances)
- partial vs full release capacity management
- check_status when ASG has been deleted
- tag propagation
- multi-instance-type overrides
- min/max/desired capacity on acquire
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
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.utilities.aws_operations import AWSOperations

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Local helpers (mirror conftest pattern exactly)
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
    config_port.app_config = None
    return config_port


def _make_moto_aws_client(region: str = REGION) -> AWSClient:
    aws_client = MagicMock(spec=AWSClient)
    aws_client.ec2_client = boto3.client("ec2", region_name=region)
    aws_client.autoscaling_client = boto3.client("autoscaling", region_name=region)
    aws_client.sts_client = boto3.client("sts", region_name=region)
    aws_client.ssm_client = boto3.client("ssm", region_name=region)
    return aws_client


def _make_launch_template_manager(aws_client: AWSClient, logger: Any) -> Any:
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


def _make_asg_handler(aws_client: AWSClient, logger: Any, config_port: Any) -> ASGHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_launch_template_manager(aws_client, logger)
    return ASGHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


def _make_request(
    request_id: str = "req-asg-pt-001",
    requested_count: int = 2,
    resource_ids: list[str] | None = None,
    provider_data: dict[str, Any] | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-asg-pt"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _make_vpc_resources(ec2_client: Any) -> dict[str, Any]:
    """Create minimal VPC, subnet, and SG for use in templates."""
    vpc = ec2_client.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    subnet = ec2_client.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=f"{REGION}a"
    )
    subnet_id = subnet["Subnet"]["SubnetId"]
    sg = ec2_client.create_security_group(
        GroupName="asg-pt-test-sg", Description="ASG price type test SG", VpcId=vpc_id
    )
    sg_id = sg["GroupId"]
    return {"subnet_id": subnet_id, "sg_id": sg_id}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aws_client(moto_aws):
    return _make_moto_aws_client()


@pytest.fixture
def logger():
    return _make_logger()


@pytest.fixture
def config_port():
    return _make_config_port()


@pytest.fixture
def handler(aws_client, logger, config_port):
    return _make_asg_handler(aws_client, logger, config_port)


@pytest.fixture
def vpc(aws_client):
    return _make_vpc_resources(aws_client.ec2_client)


@pytest.fixture
def asg_client(aws_client):
    return aws_client.autoscaling_client


@pytest.fixture
def ec2_client(aws_client):
    return aws_client.ec2_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestASGPriceTypes:
    def test_acquire_spot_price_type(self, handler, vpc, asg_client):
        """price_type='spot' sets OnDemandPercentageAboveBaseCapacity=0 in MixedInstancesPolicy."""
        template = AWSTemplate(
            template_id="tpl-spot",
            name="test-spot",
            provider_api="ASG",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=2,
            price_type="spot",
            subnet_ids=[vpc["subnet_id"]],
            security_group_ids=[vpc["sg_id"]],
        )
        request = _make_request(request_id="asg-spot-001", requested_count=2)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        asg_name = result["resource_ids"][0]
        resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        asg = resp["AutoScalingGroups"][0]
        mip = asg["MixedInstancesPolicy"]
        on_demand_pct = mip["InstancesDistribution"]["OnDemandPercentageAboveBaseCapacity"]
        assert on_demand_pct == 0

    def test_acquire_heterogeneous_price_type(self, handler, vpc, asg_client):
        """price_type='heterogeneous' with percent_on_demand=30 sets correct percentage."""
        template = AWSTemplate(
            template_id="tpl-hetero",
            name="test-hetero",
            provider_api="ASG",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=4,
            price_type="heterogeneous",
            percent_on_demand=30,
            subnet_ids=[vpc["subnet_id"]],
            security_group_ids=[vpc["sg_id"]],
        )
        request = _make_request(request_id="asg-hetero-001", requested_count=4)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        asg_name = result["resource_ids"][0]
        resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        asg = resp["AutoScalingGroups"][0]
        mip = asg["MixedInstancesPolicy"]
        on_demand_pct = mip["InstancesDistribution"]["OnDemandPercentageAboveBaseCapacity"]
        assert on_demand_pct == 30


class TestASGRelease:
    def _acquire_and_attach_instances(
        self,
        handler: ASGHandler,
        asg_client: Any,
        ec2_client: Any,
        vpc: dict[str, Any],
        request_id: str,
        count: int,
    ) -> tuple[str, list[str]]:
        """Acquire an ASG, run real EC2 instances, attach them, return (asg_name, instance_ids)."""
        template = AWSTemplate(
            template_id="tpl-release",
            name="test-release",
            provider_api="ASG",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=count,
            price_type="ondemand",
            subnet_ids=[vpc["subnet_id"]],
            security_group_ids=[vpc["sg_id"]],
        )
        request = _make_request(request_id=request_id, requested_count=count)
        result = handler.acquire_hosts(request, template)
        asg_name = result["resource_ids"][0]

        # Run real instances in moto and attach them to the ASG
        run_resp = ec2_client.run_instances(
            ImageId="ami-12345678",
            MinCount=count,
            MaxCount=count,
            InstanceType="t3.micro",
            SubnetId=vpc["subnet_id"],
        )
        instance_ids = [i["InstanceId"] for i in run_resp["Instances"]]

        asg_client.attach_instances(
            InstanceIds=instance_ids,
            AutoScalingGroupName=asg_name,
        )

        # moto's attach_instances increments DesiredCapacity on top of the
        # acquire-set value. Reset it to exactly the number of attached instances
        # so the handler's capacity snapshot matches what we intend to release.
        asg_client.update_auto_scaling_group(
            AutoScalingGroupName=asg_name,
            DesiredCapacity=count,
        )

        return asg_name, instance_ids

    def test_release_terminates_instances_and_decrements_desired_capacity(
        self, handler, vpc, asg_client, ec2_client
    ):
        """release_hosts detaches instances and decrements DesiredCapacity."""
        asg_name, instance_ids = self._acquire_and_attach_instances(
            handler, asg_client, ec2_client, vpc, "asg-rel-001", count=2
        )

        # Confirm instances are attached
        resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        initial_desired = resp["AutoScalingGroups"][0]["DesiredCapacity"]

        handler.release_hosts(instance_ids)

        resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        # ASG may be deleted (capacity=0) or decremented — either is correct
        if resp["AutoScalingGroups"]:
            new_desired = resp["AutoScalingGroups"][0]["DesiredCapacity"]
            assert new_desired < initial_desired
        # If ASG was deleted, that's also valid (all instances released)

    def test_release_all_instances_deletes_asg(self, handler, vpc, asg_client, ec2_client):
        """Releasing all instances causes the ASG to be deleted."""
        asg_name, instance_ids = self._acquire_and_attach_instances(
            handler, asg_client, ec2_client, vpc, "asg-rel-002", count=2
        )

        handler.release_hosts(instance_ids)

        resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        assert resp["AutoScalingGroups"] == []

    def test_partial_release_decrements_desired_capacity_by_n(
        self, handler, vpc, asg_client, ec2_client
    ):
        """Releasing 1 of 3 instances decrements DesiredCapacity to 2."""
        asg_name, instance_ids = self._acquire_and_attach_instances(
            handler, asg_client, ec2_client, vpc, "asg-rel-003", count=3
        )

        # Release only the first instance
        handler.release_hosts([instance_ids[0]])

        resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        assert resp["AutoScalingGroups"], "ASG should still exist after partial release"
        new_desired = resp["AutoScalingGroups"][0]["DesiredCapacity"]
        assert new_desired == 2


class TestASGCheckStatus:
    def test_check_status_asg_in_deleting_state_returns_empty(self, handler, vpc, asg_client):
        """check_hosts_status returns [] when the ASG has been deleted."""
        template = AWSTemplate(
            template_id="tpl-status",
            name="test-status",
            provider_api="ASG",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=1,
            price_type="ondemand",
            subnet_ids=[vpc["subnet_id"]],
            security_group_ids=[vpc["sg_id"]],
        )
        request = _make_request(request_id="asg-status-001", requested_count=1)
        result = handler.acquire_hosts(request, template)
        asg_name = result["resource_ids"][0]

        # Delete the ASG directly
        asg_client.delete_auto_scaling_group(AutoScalingGroupName=asg_name, ForceDelete=True)

        status_request = _make_request(request_id="asg-status-001", resource_ids=[asg_name])
        status = handler.check_hosts_status(status_request)

        assert status == []


class TestASGTags:
    def test_acquire_asg_with_tags_propagated(self, handler, vpc, asg_client):
        """Tags set on the template appear on the ASG after acquire."""
        template = AWSTemplate(
            template_id="tpl-tags",
            name="test-tags",
            provider_api="ASG",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=1,
            price_type="ondemand",
            subnet_ids=[vpc["subnet_id"]],
            security_group_ids=[vpc["sg_id"]],
            tags={"Env": "test"},
        )
        request = _make_request(request_id="asg-tags-001", requested_count=1)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        asg_name = result["resource_ids"][0]
        resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        asg = resp["AutoScalingGroups"][0]
        tag_keys = {t["Key"] for t in asg.get("Tags", [])}
        assert "Env" in tag_keys


class TestASGInstanceTypes:
    def test_acquire_asg_with_multi_instance_types(self, handler, vpc, asg_client):
        """Multiple machine_types produce Overrides entries in MixedInstancesPolicy."""
        template = AWSTemplate(
            template_id="tpl-multi",
            name="test-multi",
            provider_api="ASG",
            machine_types={"m5.xlarge": 1, "m5.2xlarge": 2},
            image_id="ami-12345678",
            max_instances=3,
            price_type="ondemand",
            subnet_ids=[vpc["subnet_id"]],
            security_group_ids=[vpc["sg_id"]],
        )
        request = _make_request(request_id="asg-multi-001", requested_count=3)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        asg_name = result["resource_ids"][0]
        resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        asg = resp["AutoScalingGroups"][0]
        mip = asg["MixedInstancesPolicy"]
        overrides = mip["LaunchTemplate"]["Overrides"]
        override_types = {o["InstanceType"] for o in overrides}
        assert "m5.xlarge" in override_types
        assert "m5.2xlarge" in override_types

    def test_acquire_asg_min_max_size_matches_desired(self, handler, vpc, asg_client):
        """acquire with max_instances=4 sets MinSize=0, MaxSize>=4, DesiredCapacity=4."""
        template = AWSTemplate(
            template_id="tpl-sizing",
            name="test-sizing",
            provider_api="ASG",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=4,
            price_type="ondemand",
            subnet_ids=[vpc["subnet_id"]],
            security_group_ids=[vpc["sg_id"]],
        )
        request = _make_request(request_id="asg-sizing-001", requested_count=4)

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        asg_name = result["resource_ids"][0]
        resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        asg = resp["AutoScalingGroups"][0]
        assert asg["MinSize"] == 0
        assert asg["MaxSize"] >= 4
        assert asg["DesiredCapacity"] == 4
