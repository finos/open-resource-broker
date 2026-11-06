"""The launch template sweep removes only ORB templates nothing uses."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import boto3
import pytest

from orb.providers.aws.services.launch_template_sweep_service import (
    AWSLaunchTemplateSweepService,
    SweepAborted,
)
from tests.providers.aws.mocked.test_cleanup_e2e import (
    _make_aws_client,
    _make_cleanup_config_port,
    _make_logger,
)

REGION = "eu-west-2"
ORB_TAGS = [
    {"Key": "orb:managed-by", "Value": "open-resource-broker"},
]

pytestmark = [pytest.mark.moto]


PREFIX = "orb-"


def _template(
    ec2, name: str, request_id: str | None, managed: bool = True, orb_named: bool = True
) -> str:
    """Create a template; by default it is named the way ORB names the ones it creates."""
    tags = list(ORB_TAGS) if managed else []
    if request_id:
        tags.append({"Key": "orb:request-id", "Value": request_id})
        tags.append({"Key": "orb:template-id", "Value": name})
    if request_id and orb_named:
        name = f"{PREFIX}{request_id}-{name}"
    return ec2.create_launch_template(
        LaunchTemplateName=name,
        LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.micro"},
        TagSpecifications=[{"ResourceType": "launch-template", "Tags": tags}] if tags else [],
    )["LaunchTemplate"]["LaunchTemplateId"]


def _service(aws_client) -> AWSLaunchTemplateSweepService:
    return AWSLaunchTemplateSweepService(
        aws_client, _make_logger(), _make_cleanup_config_port(PREFIX)
    )


def _exists(ec2, lt_id: str) -> bool:
    return bool(
        [
            t
            for t in ec2.describe_launch_templates()["LaunchTemplates"]
            if t["LaunchTemplateId"] == lt_id
        ]
    )


@pytest.fixture
def ec2(moto_aws):
    return boto3.client("ec2", region_name=REGION)


@pytest.fixture
def aws_client(moto_aws):
    return _make_aws_client()


def test_an_unused_orb_template_is_deleted_and_everything_else_is_kept(
    ec2, aws_client, moto_vpc_resources
):
    orphan = _template(ec2, "orphan", "req-orphan")
    in_use = _template(ec2, "in-use", "req-live")
    users = _template(ec2, "users-own", None, managed=False)
    ec2.run_instances(
        MinCount=1,
        MaxCount=1,
        SubnetId=moto_vpc_resources["subnet_ids"][0],
        LaunchTemplate={"LaunchTemplateId": in_use, "Version": "1"},
    )

    report = _service(aws_client).sweep(dry_run=False, min_age=timedelta(0))

    assert [d["launch_template_id"] for d in report["deleted"]] == [orphan]
    assert not _exists(ec2, orphan)
    assert _exists(ec2, in_use)
    assert _exists(ec2, users)
    kept = {k["launch_template_id"]: k["reason"] for k in report["kept"]}
    assert kept == {in_use: "live instances were launched from it"}


def test_dry_run_reports_without_deleting(ec2, aws_client):
    orphan = _template(ec2, "orphan", "req-orphan")

    report = _service(aws_client).sweep(dry_run=True, min_age=timedelta(0))

    assert [d["launch_template_id"] for d in report["would_delete"]] == [orphan]
    assert report["deleted"] == []
    assert _exists(ec2, orphan)


def test_a_young_template_is_kept(ec2, aws_client):
    young = _template(ec2, "young", "req-young")

    report = _service(aws_client).sweep(dry_run=False, min_age=timedelta(hours=24))

    assert report["deleted"] == []
    assert report["kept"][0]["reason"] == "younger than the minimum age"
    assert _exists(ec2, young)


def test_a_template_referenced_by_an_active_group_is_kept(ec2, aws_client, moto_vpc_resources):
    referenced = _template(ec2, "group-template", "req-group")
    boto3.client("autoscaling", region_name=REGION).create_auto_scaling_group(
        AutoScalingGroupName="req-group",
        LaunchTemplate={"LaunchTemplateId": referenced, "Version": "1"},
        MinSize=0,
        MaxSize=1,
        DesiredCapacity=0,
        VPCZoneIdentifier=moto_vpc_resources["subnet_ids"][0],
    )

    report = _service(aws_client).sweep(dry_run=False, min_age=timedelta(0))

    assert report["deleted"] == []
    assert report["kept"][0]["reason"] == "referenced by an active group or fleet"
    assert _exists(ec2, referenced)


def test_a_lookup_failure_aborts_without_deleting(ec2, aws_client):
    orphan = _template(ec2, "orphan", "req-orphan")
    aws_client.autoscaling_client = MagicMock()
    aws_client.autoscaling_client.describe_auto_scaling_groups.side_effect = RuntimeError("down")

    service = _service(aws_client)
    with pytest.raises(SweepAborted):
        service.sweep(dry_run=False, min_age=timedelta(0))
    assert _exists(ec2, orphan)


def test_a_disabled_cleanup_configuration_deletes_nothing(ec2, aws_client):
    from orb.config.schemas.cleanup_schema import CleanupConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults

    orphan = _template(ec2, "orphan", "req-orphan")
    config_port = _make_cleanup_config_port(PREFIX)
    config_port.get_provider_config.return_value.provider_defaults = {
        "aws": ProviderDefaults(cleanup=CleanupConfig(enabled=False).model_dump())
    }
    service = AWSLaunchTemplateSweepService(aws_client, _make_logger(), config_port)

    report = service.sweep(dry_run=False, min_age=timedelta(0))

    assert report["deleted"] == [] and "disabled" in report["message"]
    assert _exists(ec2, orphan)


@pytest.mark.asyncio
async def test_the_provider_operation_reports_the_sweep(ec2, aws_client):
    from orb.domain.base.operations import Operation, OperationType

    orphan = _template(ec2, "orphan", "req-orphan")
    service = _service(aws_client)

    result = await service.cleanup_orphaned_templates(
        Operation(
            OperationType.CLEANUP_ORPHANED_RESOURCES,
            {"dry_run": False, "min_age_hours": 0, "force": True},
        )
    )

    assert result.success
    assert [d["launch_template_id"] for d in result.data["deleted"]] == [orphan]


@pytest.mark.asyncio
async def test_an_invalid_age_is_rejected(aws_client):
    from orb.domain.base.operations import Operation, OperationType

    result = await _service(aws_client).cleanup_orphaned_templates(
        Operation(OperationType.CLEANUP_ORPHANED_RESOURCES, {"min_age_hours": -1})
    )

    assert result.success is False
    assert result.error_code == "INVALID_PARAMETERS"


@pytest.mark.asyncio
async def test_an_age_below_the_floor_needs_force(ec2, aws_client):
    from orb.domain.base.operations import Operation, OperationType

    orphan = _template(ec2, "orphan", "req-orphan")

    result = await _service(aws_client).cleanup_orphaned_templates(
        Operation(OperationType.CLEANUP_ORPHANED_RESOURCES, {"min_age_hours": 0})
    )

    assert result.success is False
    assert result.error_code == "INVALID_PARAMETERS"
    assert _exists(ec2, orphan)


def test_a_lookup_failing_on_a_later_template_deletes_nothing(ec2, aws_client):
    """Decisions are made for every template before the first delete."""
    first = _template(ec2, "first", "req-first")
    second = _template(ec2, "second", "req-second")
    service = _service(aws_client)
    real = service._has_live_instances
    calls = {"n": 0}

    def flaky(filter_name, value):
        calls["n"] += 1
        if value == "req-second" or (calls["n"] > 2 and filter_name.startswith("tag:aws")):
            raise SweepAborted("describe_instances failed: throttled")
        return real(filter_name, value)

    service._has_live_instances = flaky  # type: ignore[method-assign]

    with pytest.raises(SweepAborted):
        service.sweep(dry_run=False, min_age=timedelta(0))
    assert _exists(ec2, first) and _exists(ec2, second)


@pytest.mark.asyncio
async def test_a_failed_delete_returns_a_truthful_partial_report(ec2, aws_client):
    from botocore.exceptions import ClientError

    from orb.domain.base.operations import Operation, OperationType

    first = _template(ec2, "first", "req-first")
    second = _template(ec2, "second", "req-second")
    service = _service(aws_client)
    real = service._delete_template

    def delete(lt_id):
        if lt_id == second:
            raise ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "no"}}, "x")
        real(lt_id)

    service._delete_template = delete  # type: ignore[method-assign]

    result = await service.cleanup_orphaned_templates(
        Operation(
            OperationType.CLEANUP_ORPHANED_RESOURCES,
            {"min_age_hours": 0, "force": True},
        )
    )

    assert result.success is False
    assert result.error_code == "LAUNCH_TEMPLATE_SWEEP_PARTIAL"
    assert [d["launch_template_id"] for d in result.data["deleted"]] == [first]
    assert [f["launch_template_id"] for f in result.data["failed"]] == [second]
    assert "nothing was deleted" not in (result.error_message or "")
    assert not _exists(ec2, first) and _exists(ec2, second)


def test_a_customer_template_carrying_the_orb_tags_survives(ec2, aws_client):
    mine = _template(ec2, "my-user-template", "req-1", orb_named=False)
    generated = _template(ec2, "tpl", "req-1")

    report = _service(aws_client).sweep(dry_run=False, min_age=timedelta(0))

    assert [d["launch_template_id"] for d in report["deleted"]] == [generated]
    assert _exists(ec2, mine)
    kept = {k["launch_template_id"]: k["reason"] for k in report["kept"]}
    assert kept == {mine: "name is not ORB-generated"}


def test_a_name_must_match_the_request_and_template_tags_exactly(ec2, aws_client):
    """A template of request R1-extra is not matched by the tags of request R1."""
    other = ec2.create_launch_template(
        LaunchTemplateName="orb-R1-extra-tpl",
        LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.micro"},
        TagSpecifications=[
            {
                "ResourceType": "launch-template",
                "Tags": [
                    *ORB_TAGS,
                    {"Key": "orb:request-id", "Value": "R1"},
                    {"Key": "orb:template-id", "Value": "tpl"},
                ],
            }
        ],
    )["LaunchTemplate"]["LaunchTemplateId"]

    report = _service(aws_client).sweep(dry_run=False, min_age=timedelta(0))

    assert report["deleted"] == []
    assert _exists(ec2, other)


def test_without_a_config_port_nothing_is_deleted(ec2, aws_client):
    orphan = _template(ec2, "tpl", "req-1")
    service = AWSLaunchTemplateSweepService(aws_client, _make_logger(), None)

    report = service.sweep(dry_run=False, min_age=timedelta(0))

    assert report["deleted"] == [] and report["refused"] is True
    assert _exists(ec2, orphan)


def test_an_empty_resource_prefix_still_matches_the_exact_generated_name(ec2, aws_client):
    """With no prefix ORB names templates '<request id>-<template id>'."""
    generated = ec2.create_launch_template(
        LaunchTemplateName="req-1-tpl",
        LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.micro"},
        TagSpecifications=[
            {
                "ResourceType": "launch-template",
                "Tags": [
                    *ORB_TAGS,
                    {"Key": "orb:request-id", "Value": "req-1"},
                    {"Key": "orb:template-id", "Value": "tpl"},
                ],
            }
        ],
    )["LaunchTemplate"]["LaunchTemplateId"]
    customer = _template(ec2, "my-user-template", "req-2", orb_named=False)
    service = AWSLaunchTemplateSweepService(
        aws_client, _make_logger(), _make_cleanup_config_port("")
    )

    report = service.sweep(dry_run=False, min_age=timedelta(0))

    assert [d["launch_template_id"] for d in report["deleted"]] == [generated]
    assert _exists(ec2, customer)
    assert {k["launch_template_id"]: k["reason"] for k in report["kept"]} == {
        customer: "name is not ORB-generated"
    }


@pytest.mark.asyncio
async def test_the_refusal_reason_is_in_the_result(ec2, aws_client):
    from orb.domain.base.operations import Operation, OperationType

    orphan = _template(ec2, "tpl", "req-1")
    service = AWSLaunchTemplateSweepService(aws_client, _make_logger(), None)

    result = await service.cleanup_orphaned_templates(
        Operation(OperationType.CLEANUP_ORPHANED_RESOURCES, {"dry_run": False})
    )

    assert result.success is False
    assert result.error_code == "LAUNCH_TEMPLATE_CLEANUP_REFUSED"
    assert "nothing was deleted" in (result.error_message or "")
    assert _exists(ec2, orphan)


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, "False", "false", "no", "off", 0, "0", None])
async def test_a_false_force_never_removes_the_age_floor(ec2, aws_client, force):
    from orb.domain.base.operations import Operation, OperationType

    orphan = _template(ec2, "tpl", "req-1")

    result = await _service(aws_client).cleanup_orphaned_templates(
        Operation(OperationType.CLEANUP_ORPHANED_RESOURCES, {"min_age_hours": 0, "force": force})
    )

    assert result.success is False
    assert result.error_code == "INVALID_PARAMETERS"
    assert _exists(ec2, orphan)


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", ["no", "off", 1, "yes"])
async def test_a_non_boolean_dry_run_is_rejected(ec2, aws_client, dry_run):
    from orb.domain.base.operations import Operation, OperationType

    orphan = _template(ec2, "tpl", "req-1")

    result = await _service(aws_client).cleanup_orphaned_templates(
        Operation(OperationType.CLEANUP_ORPHANED_RESOURCES, {"dry_run": dry_run})
    )

    assert result.error_code == "INVALID_PARAMETERS"
    assert _exists(ec2, orphan)


@pytest.mark.asyncio
async def test_unknown_parameters_are_rejected(ec2, aws_client):
    from orb.domain.base.operations import Operation, OperationType

    orphan = _template(ec2, "tpl", "req-1")

    result = await _service(aws_client).cleanup_orphaned_templates(
        Operation(OperationType.CLEANUP_ORPHANED_RESOURCES, {"min_age_hour": 0})
    )

    assert result.error_code == "INVALID_PARAMETERS"
    assert "min_age_hour" in (result.error_message or "")
    assert _exists(ec2, orphan)


@pytest.mark.asyncio
async def test_string_booleans_are_accepted_case_insensitively(ec2, aws_client):
    from orb.domain.base.operations import Operation, OperationType

    orphan = _template(ec2, "tpl", "req-1")

    result = await _service(aws_client).cleanup_orphaned_templates(
        Operation(
            OperationType.CLEANUP_ORPHANED_RESOURCES,
            {"min_age_hours": 0, "force": "True", "dry_run": "TRUE"},
        )
    )

    assert result.success
    assert [d["launch_template_id"] for d in result.data["would_delete"]] == [orphan]
    assert _exists(ec2, orphan)


@pytest.mark.asyncio
async def test_the_result_names_the_region_and_account(ec2, aws_client):
    from orb.domain.base.operations import Operation, OperationType

    aws_client.region_name = REGION

    result = await _service(aws_client).cleanup_orphaned_templates(
        Operation(OperationType.CLEANUP_ORPHANED_RESOURCES, {"dry_run": True})
    )

    assert result.data["region"] == REGION
    assert result.data["account_id"]


@pytest.mark.asyncio
async def test_disabled_cleanup_is_not_reported_as_success(ec2, aws_client):
    from orb.config.schemas.cleanup_schema import CleanupConfig
    from orb.config.schemas.provider_strategy_schema import ProviderDefaults
    from orb.domain.base.operations import Operation, OperationType

    config_port = _make_cleanup_config_port(PREFIX)
    config_port.get_provider_config.return_value.provider_defaults = {
        "aws": ProviderDefaults(cleanup=CleanupConfig(enabled=False).model_dump())
    }
    service = AWSLaunchTemplateSweepService(aws_client, _make_logger(), config_port)

    result = await service.cleanup_orphaned_templates(
        Operation(OperationType.CLEANUP_ORPHANED_RESOURCES, {"dry_run": False})
    )

    assert result.success is False
    assert result.error_code == "LAUNCH_TEMPLATE_CLEANUP_DISABLED"
