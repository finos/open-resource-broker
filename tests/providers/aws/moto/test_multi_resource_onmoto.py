"""Multi-resource batch return integration tests against moto-mocked AWS.

Tests the scenario where instances from two independent handlers are collected
and released together, verifying that all resources are properly cleaned up.

Covers:
- RunInstances: two handlers acquire instances; all are terminated in one batch
- ASG: two handlers acquire ASGs; instances are attached and released per-handler;
  both ASGs end up with DesiredCapacity==0 (deleted)
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
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from orb.providers.aws.utilities.aws_operations import AWSOperations

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Helpers (mirror conftest / test_asg_price_types pattern exactly)
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


def _make_launch_template_manager(aws_client: AWSClient, logger: Any) -> AWSLaunchTemplateManager:
    from orb.providers.aws.infrastructure.launch_template.manager import LaunchTemplateResult

    lt_manager = MagicMock(spec=AWSLaunchTemplateManager)

    def _create_or_update(template: AWSTemplate, request: Any) -> LaunchTemplateResult:
        lt_name = f"orb-lt-{request.request_id}-{template.template_id}"
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


def _make_run_instances_handler(
    aws_client: AWSClient, logger: Any, config_port: Any
) -> RunInstancesHandler:
    aws_ops = AWSOperations(aws_client, logger, config_port)
    lt_manager = _make_launch_template_manager(aws_client, logger)
    return RunInstancesHandler(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=lt_manager,
        config_port=config_port,
    )


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
    request_id: str = "req-multi-001",
    requested_count: int = 1,
    resource_ids: list[str] | None = None,
    provider_data: dict[str, Any] | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-multi"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _make_vpc_resources(ec2_client: Any) -> dict[str, Any]:
    vpc = ec2_client.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    subnet = ec2_client.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=f"{REGION}a"
    )
    subnet_id = subnet["Subnet"]["SubnetId"]
    sg = ec2_client.create_security_group(
        GroupName="multi-test-sg", Description="Multi resource test SG", VpcId=vpc_id
    )
    sg_id = sg["GroupId"]
    return {"subnet_id": subnet_id, "sg_id": sg_id}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aws_client(moto_aws: Any) -> AWSClient:
    return _make_moto_aws_client()


@pytest.fixture
def logger() -> Any:
    return _make_logger()


@pytest.fixture
def config_port() -> Any:
    return _make_config_port()


@pytest.fixture
def vpc(aws_client: AWSClient) -> dict[str, Any]:
    return _make_vpc_resources(aws_client.ec2_client)


@pytest.fixture
def ec2_client(aws_client: AWSClient) -> Any:
    return aws_client.ec2_client


@pytest.fixture
def asg_client(aws_client: AWSClient) -> Any:
    return aws_client.autoscaling_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMultiRunInstancesBatchReturn:
    def test_multi_run_instances_batch_return(
        self,
        aws_client: AWSClient,
        logger: Any,
        config_port: Any,
        vpc: dict[str, Any],
        ec2_client: Any,
    ) -> None:
        """Two RunInstancesHandlers acquire one instance each; releasing all instance_ids
        in a single call terminates every instance."""
        # Arrange — two handlers with distinct template configs
        handler_a = _make_run_instances_handler(aws_client, logger, config_port)
        handler_b = _make_run_instances_handler(aws_client, logger, config_port)

        template_a = AWSTemplate(
            template_id="tpl-run-a",
            name="test-run-a",
            provider_api="RunInstances",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=2,
            price_type="ondemand",
            subnet_ids=[vpc["subnet_id"]],
            security_group_ids=[vpc["sg_id"]],
            tags={"Handler": "a"},
        )
        template_b = AWSTemplate(
            template_id="tpl-run-b",
            name="test-run-b",
            provider_api="RunInstances",
            machine_types={"t3.small": 1},
            image_id="ami-12345678",
            max_instances=2,
            price_type="ondemand",
            subnet_ids=[vpc["subnet_id"]],
            security_group_ids=[vpc["sg_id"]],
            tags={"Handler": "b"},
        )

        request_a = _make_request(request_id="multi-run-a", requested_count=1)
        request_b = _make_request(request_id="multi-run-b", requested_count=1)

        # Act — acquire from each handler independently
        result_a = handler_a.acquire_hosts(request_a, template_a)
        result_b = handler_b.acquire_hosts(request_b, template_b)

        assert result_a["success"] is True
        assert result_b["success"] is True

        instance_ids_a: list[str] = result_a["provider_data"]["instance_ids"]
        instance_ids_b: list[str] = result_b["provider_data"]["instance_ids"]
        assert len(instance_ids_a) >= 1
        assert len(instance_ids_b) >= 1

        all_instance_ids = instance_ids_a + instance_ids_b

        # Act — batch release via handler_a (any handler can terminate arbitrary instance IDs)
        handler_a.release_hosts(all_instance_ids)

        # Assert — all instances are terminated
        resp = ec2_client.describe_instances(InstanceIds=all_instance_ids)
        states = [
            instance["State"]["Name"]
            for reservation in resp["Reservations"]
            for instance in reservation["Instances"]
        ]
        assert len(states) == len(all_instance_ids)
        assert all(s in ("shutting-down", "terminated") for s in states), (
            f"Expected all instances terminated, got states: {states}"
        )


class TestMultiASGBatchReturn:
    def _acquire_and_attach(
        self,
        handler: ASGHandler,
        asg_client: Any,
        ec2_client: Any,
        vpc: dict[str, Any],
        request_id: str,
        template_id: str,
        count: int = 1,
    ) -> tuple[str, list[str]]:
        """Acquire an ASG, run real EC2 instances in moto, attach them, return (asg_name, instance_ids)."""
        template = AWSTemplate(
            template_id=template_id,
            name=f"test-asg-{template_id}",
            provider_api="ASG",
            machine_types={"t3.micro": 1},
            image_id="ami-12345678",
            max_instances=count,
            price_type="ondemand",
            subnet_ids=[vpc["subnet_id"]],
            security_group_ids=[vpc["sg_id"]],
            tags={"Handler": template_id},
        )
        request = _make_request(request_id=request_id, requested_count=count)
        result = handler.acquire_hosts(request, template)
        assert result["success"] is True
        asg_name: str = result["resource_ids"][0]

        # moto does not auto-spin ASG instances — run and attach manually
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
        # moto increments DesiredCapacity on attach — reset to the intended count
        asg_client.update_auto_scaling_group(
            AutoScalingGroupName=asg_name,
            DesiredCapacity=count,
        )

        return asg_name, instance_ids

    def test_multi_asg_batch_return_decrements_both_asgs(
        self,
        aws_client: AWSClient,
        logger: Any,
        config_port: Any,
        vpc: dict[str, Any],
        asg_client: Any,
        ec2_client: Any,
    ) -> None:
        """Two ASGHandlers each acquire an ASG with one attached instance.
        Releasing each handler's instances causes both ASGs to reach DesiredCapacity==0
        (deleted by the handler)."""
        # Arrange — two independent handlers
        handler_a = _make_asg_handler(aws_client, logger, config_port)
        handler_b = _make_asg_handler(aws_client, logger, config_port)

        asg_name_a, instance_ids_a = self._acquire_and_attach(
            handler_a,
            asg_client,
            ec2_client,
            vpc,
            request_id="multi-asg-a",
            template_id="tpl-asg-a",
            count=1,
        )
        asg_name_b, instance_ids_b = self._acquire_and_attach(
            handler_b,
            asg_client,
            ec2_client,
            vpc,
            request_id="multi-asg-b",
            template_id="tpl-asg-b",
            count=1,
        )

        # Confirm both ASGs exist with DesiredCapacity==1 before release
        for asg_name in (asg_name_a, asg_name_b):
            resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
            assert resp["AutoScalingGroups"], f"ASG {asg_name} should exist before release"
            assert resp["AutoScalingGroups"][0]["DesiredCapacity"] == 1

        # Act — release each handler's instances separately
        handler_a.release_hosts(instance_ids_a)
        handler_b.release_hosts(instance_ids_b)

        # Assert — both ASGs are deleted (DesiredCapacity reached 0)
        for asg_name in (asg_name_a, asg_name_b):
            resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
            assert resp["AutoScalingGroups"] == [], (
                f"ASG {asg_name} should be deleted after all instances released, "
                f"but still exists with DesiredCapacity="
                f"{resp['AutoScalingGroups'][0]['DesiredCapacity'] if resp['AutoScalingGroups'] else 'N/A'}"
            )
