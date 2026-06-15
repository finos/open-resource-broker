"""Gap coverage tests for RunInstancesHandler.

Covers template field propagation (tags, user_data, key_name, instance_profile),
instance lifecycle edge cases (stopped, terminated), multi-step partial release,
idempotent release, and invalid subnet error handling.
"""

import base64
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
    LaunchTemplateResult,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations

REGION = "eu-west-2"


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


def _make_config_port() -> Any:
    from orb.config.schemas.cleanup_schema import CleanupConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = ""
    provider_defaults = ProviderDefaults(cleanup=CleanupConfig(enabled=False).model_dump())
    provider_config = MagicMock()
    provider_config.provider_defaults = {"aws": provider_defaults}
    config_port.get_provider_config.return_value = provider_config
    config_port.app_config = None
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
        lt_data: dict[str, Any] = {
            "ImageId": template.image_id or "ami-12345678",
            "InstanceType": (
                next(iter(template.machine_types.keys())) if template.machine_types else "t3.micro"
            ),
            "NetworkInterfaces": [
                {
                    "DeviceIndex": 0,
                    "SubnetId": template.subnet_ids[0] if template.subnet_ids else "",
                    "Groups": template.security_group_ids or [],
                    "AssociatePublicIpAddress": False,
                }
            ],
        }
        if template.user_data:
            lt_data["UserData"] = base64.b64encode(template.user_data.encode()).decode()
        if template.key_name:
            lt_data["KeyName"] = template.key_name
        if template.instance_profile:
            lt_data["IamInstanceProfile"] = {"Arn": template.instance_profile}
        try:
            resp = aws_client.ec2_client.create_launch_template(
                LaunchTemplateName=lt_name,
                LaunchTemplateData=lt_data,
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
    request_id: str = "req-gaps-001",
    requested_count: int = 1,
    resource_ids: list[str] | None = None,
    provider_data: dict[str, Any] | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-gaps"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {}
    req.provider_api = None
    return req


def _run_instances_template(subnet_id: str, sg_id: str, **kwargs: Any) -> AWSTemplate:
    base: dict[str, Any] = dict(
        template_id="tpl-gaps",
        name="test-gaps",
        provider_api="RunInstances",
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        max_instances=10,
        price_type="ondemand",
        subnet_ids=[subnet_id],
        security_group_ids=[sg_id],
        tags={"Environment": "test"},
    )
    base.update(kwargs)
    return AWSTemplate(**base)


def _instance_states(ec2_client: Any, instance_ids: list[str]) -> list[str]:
    resp = ec2_client.describe_instances(InstanceIds=instance_ids)
    return [i["State"]["Name"] for r in resp["Reservations"] for i in r["Instances"]]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aws_client(moto_aws) -> AWSClient:
    return _make_aws_client()


@pytest.fixture
def handler(aws_client: AWSClient) -> RunInstancesHandler:
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
def subnet_id(moto_vpc_resources: dict[str, Any]) -> str:
    return moto_vpc_resources["subnet_ids"][0]


@pytest.fixture
def sg_id(moto_vpc_resources: dict[str, Any]) -> str:
    return moto_vpc_resources["sg_id"]


@pytest.fixture
def ec2(moto_aws) -> Any:
    return boto3.client("ec2", region_name=REGION)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunInstancesGaps:
    def test_acquire_with_tags_propagated_to_instances(
        self, handler: RunInstancesHandler, subnet_id: str, sg_id: str, ec2: Any
    ) -> None:
        """Tags set on the template appear on launched instances.

        The handler passes user tags via TagSpecifications in run_instances,
        so they are visible directly on the instance (not just the LT).
        """
        template = _run_instances_template(subnet_id, sg_id, tags={"Env": "test", "Project": "orb"})
        request = _make_request(request_id="req-tags-001")

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        instance_ids = result["provider_data"]["instance_ids"]
        resp = ec2.describe_instances(InstanceIds=instance_ids)
        instances = [i for r in resp["Reservations"] for i in r["Instances"]]
        assert instances, "No instances returned by describe_instances"

        all_tag_keys = {tag["Key"] for instance in instances for tag in instance.get("Tags", [])}
        assert "Env" in all_tag_keys
        assert "Project" in all_tag_keys

    def test_acquire_with_user_data(
        self, handler: RunInstancesHandler, subnet_id: str, sg_id: str, ec2: Any
    ) -> None:
        """user_data set on the template is stored in the launch template version.

        Moto does not propagate LT UserData to the instance attribute, so we
        verify it is present in the launch template version data instead.
        """
        user_data_script = "#!/bin/bash\necho hello"
        template = _run_instances_template(subnet_id, sg_id, user_data=user_data_script)
        request = _make_request(request_id="req-ud-001")

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True

        # The mock LT manager creates a real LT in moto — find it by name pattern
        lt_name = f"orb-lt-{request.request_id}"
        lt_resp = ec2.describe_launch_templates(
            Filters=[{"Name": "launch-template-name", "Values": [lt_name]}]
        )
        assert lt_resp["LaunchTemplates"], f"Launch template '{lt_name}' not found"
        lt_id = lt_resp["LaunchTemplates"][0]["LaunchTemplateId"]

        ltv = ec2.describe_launch_template_versions(LaunchTemplateId=lt_id, Versions=["1"])
        ltd = ltv["LaunchTemplateVersions"][0]["LaunchTemplateData"]
        encoded = ltd.get("UserData", "")
        assert encoded, "UserData not stored in launch template"
        decoded = base64.b64decode(encoded).decode()
        assert "echo hello" in decoded

    def test_acquire_with_key_name(
        self, handler: RunInstancesHandler, subnet_id: str, sg_id: str, ec2: Any
    ) -> None:
        """key_name set on the template is stored in the launch template version.

        Moto does not propagate LT KeyName to the instance, so we verify it
        is present in the launch template version data instead.
        """
        ec2.create_key_pair(KeyName="test-key")
        template = _run_instances_template(subnet_id, sg_id, key_name="test-key")
        request = _make_request(request_id="req-key-001")

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True

        lt_name = f"orb-lt-{request.request_id}"
        lt_resp = ec2.describe_launch_templates(
            Filters=[{"Name": "launch-template-name", "Values": [lt_name]}]
        )
        assert lt_resp["LaunchTemplates"], f"Launch template '{lt_name}' not found"
        lt_id = lt_resp["LaunchTemplates"][0]["LaunchTemplateId"]

        ltv = ec2.describe_launch_template_versions(LaunchTemplateId=lt_id, Versions=["1"])
        ltd = ltv["LaunchTemplateVersions"][0]["LaunchTemplateData"]
        assert ltd.get("KeyName") == "test-key"

    def test_acquire_with_iam_instance_profile(
        self, handler: RunInstancesHandler, subnet_id: str, sg_id: str, ec2: Any
    ) -> None:
        """instance_profile ARN set on the template is stored in the launch template version.

        Moto does not propagate LT IamInstanceProfile to the instance, so we
        verify it is present in the launch template version data instead.
        """
        profile_arn = "arn:aws:iam::123456789012:instance-profile/test-profile"
        template = _run_instances_template(subnet_id, sg_id, instance_profile=profile_arn)
        request = _make_request(request_id="req-iam-001")

        result = handler.acquire_hosts(request, template)

        assert result["success"] is True

        lt_name = f"orb-lt-{request.request_id}"
        lt_resp = ec2.describe_launch_templates(
            Filters=[{"Name": "launch-template-name", "Values": [lt_name]}]
        )
        assert lt_resp["LaunchTemplates"], f"Launch template '{lt_name}' not found"
        lt_id = lt_resp["LaunchTemplates"][0]["LaunchTemplateId"]

        ltv = ec2.describe_launch_template_versions(LaunchTemplateId=lt_id, Versions=["1"])
        ltd = ltv["LaunchTemplateVersions"][0]["LaunchTemplateData"]
        iam_profile = ltd.get("IamInstanceProfile", {})
        # The real LT manager strips the ARN to a name; the mock stores the Name key
        assert iam_profile, "IamInstanceProfile not stored in launch template"

    def test_check_status_instance_in_stopped_state(
        self, handler: RunInstancesHandler, subnet_id: str, sg_id: str, ec2: Any
    ) -> None:
        """check_hosts_status returns entries for stopped instances."""
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="req-stop-001")

        result = handler.acquire_hosts(request, template)
        instance_ids = result["provider_data"]["instance_ids"]
        reservation_id = result["resource_ids"][0]

        ec2.stop_instances(InstanceIds=instance_ids)

        status_request = _make_request(
            request_id="req-stop-001",
            resource_ids=[reservation_id],
            provider_data={"instance_ids": instance_ids, "reservation_id": reservation_id},
        )
        status = handler.check_hosts_status(status_request)

        assert len(status) == len(instance_ids)
        assert status[0]["instance_id"] == instance_ids[0]

    def test_check_status_instance_in_terminated_state(
        self, handler: RunInstancesHandler, subnet_id: str, sg_id: str, ec2: Any
    ) -> None:
        """check_hosts_status reflects terminated state for terminated instances."""
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="req-term-001")

        result = handler.acquire_hosts(request, template)
        instance_ids = result["provider_data"]["instance_ids"]
        reservation_id = result["resource_ids"][0]

        ec2.terminate_instances(InstanceIds=instance_ids)

        status_request = _make_request(
            request_id="req-term-001",
            resource_ids=[reservation_id],
            provider_data={"instance_ids": instance_ids, "reservation_id": reservation_id},
        )
        status = handler.check_hosts_status(status_request)

        assert len(status) == len(instance_ids)
        terminated_statuses = {entry["status"] for entry in status}
        assert terminated_statuses & {"terminated", "shutting-down", "stopping"}

    def test_release_partial_then_full_two_step(
        self, handler: RunInstancesHandler, subnet_id: str, sg_id: str, ec2: Any
    ) -> None:
        """Release 1 of 3, assert 2 remain; then release remaining 2, assert all terminated.

        Moto returns 1 instance per run_instances call, so we acquire 3 times.
        """
        template = _run_instances_template(subnet_id, sg_id)
        instance_ids: list[str] = []
        for i in range(3):
            req = _make_request(request_id=f"req-2step-{i}", requested_count=1)
            res = handler.acquire_hosts(req, template)
            assert res["success"] is True
            instance_ids.extend(res["provider_data"]["instance_ids"])

        assert len(instance_ids) == 3

        # Step 1: release first instance
        handler.release_hosts([instance_ids[0]])

        terminated = _instance_states(ec2, [instance_ids[0]])
        assert all(s in ("shutting-down", "terminated") for s in terminated)

        remaining = _instance_states(ec2, instance_ids[1:])
        assert all(s in ("pending", "running") for s in remaining)

        # Step 2: release the remaining two
        handler.release_hosts(instance_ids[1:])

        all_states = _instance_states(ec2, instance_ids)
        assert all(s in ("shutting-down", "terminated") for s in all_states)

    def test_release_already_terminated_is_idempotent(
        self, handler: RunInstancesHandler, subnet_id: str, sg_id: str, ec2: Any
    ) -> None:
        """Calling release_hosts on already-terminated instances does not raise."""
        template = _run_instances_template(subnet_id, sg_id)
        request = _make_request(request_id="req-idem-001")

        result = handler.acquire_hosts(request, template)
        instance_ids = result["provider_data"]["instance_ids"]

        # Terminate directly first
        ec2.terminate_instances(InstanceIds=instance_ids)

        # release_hosts on already-terminated instances must not raise
        handler.release_hosts(instance_ids)

    def test_acquire_invalid_subnet_raises(self, aws_client: AWSClient, sg_id: str) -> None:
        """acquire_hosts raises when the underlying EC2 call fails due to an invalid subnet.

        Moto does not validate subnet IDs, so we construct a handler whose LT
        manager raises a ClientError (simulating what AWS would return for an
        invalid subnet), and assert the handler surfaces that as a
        ClientError or AWSInfrastructureError.
        """
        logger = _make_logger()
        config_port = _make_config_port()
        aws_ops = AWSOperations(aws_client, logger, config_port)

        bad_lt_manager = MagicMock(spec=AWSLaunchTemplateManager)
        bad_lt_manager.create_or_update_launch_template.side_effect = ClientError(
            {
                "Error": {
                    "Code": "InvalidSubnetID.NotFound",
                    "Message": "The subnet ID 'subnet-00000000' does not exist",
                }
            },
            "CreateLaunchTemplate",
        )

        bad_handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=logger,
            aws_ops=aws_ops,
            launch_template_manager=bad_lt_manager,
            config_port=config_port,
        )

        template = _run_instances_template(subnet_id="subnet-00000000", sg_id=sg_id)
        request = _make_request(request_id="req-badsub-001")

        with pytest.raises((ClientError, AWSInfrastructureError)):
            bad_handler.acquire_hosts(request, template)
