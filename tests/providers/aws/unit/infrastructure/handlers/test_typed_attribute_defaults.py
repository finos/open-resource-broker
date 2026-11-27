"""Default-path behaviour for fields read straight off typed templates and requests.

Each case builds real ``AWSTemplate`` / ``Request`` objects and leaves the
optional field at its default, which is the path a missing ``getattr`` default
used to cover.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.template_aggregate import Template
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.adapters.template_adapter import AWSTemplateAdapter
from orb.providers.aws.infrastructure.handlers.asg.config_builder import ASGConfigBuilder
from orb.providers.aws.infrastructure.handlers.spot_fleet.config_builder import (
    SpotFleetConfigBuilder,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler
from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager

pytestmark = pytest.mark.unit


def _template(**overrides: Any) -> AWSTemplate:
    fields: dict[str, Any] = dict(
        template_id="tpl-1",
        image_id="ami-12345678",
        subnet_ids=["subnet-1"],
        security_group_ids=["sg-1"],
        machine_types={"t3.micro": 1},
        price_type="spot",
    )
    fields.update(overrides)
    return AWSTemplate(**fields)


def _request(**overrides: Any) -> Request:
    fields: dict[str, Any] = dict(
        request_id=RequestId(value="req-00000000-0000-0000-0000-000000000123"),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        template_id="tpl-1",
        requested_count=3,
    )
    fields.update(overrides)
    return Request(**fields)


def test_default_template_fields_are_none():
    template = _template()

    assert template.percent_on_demand is None
    assert template.spot_fleet_request_expiry is None
    assert template.iops is None
    assert template.monitoring_enabled is None
    assert template.provider_config is None


@pytest.mark.parametrize("builder_cls", [ASGConfigBuilder, SpotFleetConfigBuilder])
def test_capacity_distribution_without_percent_on_demand(builder_cls):
    builder = builder_cls(native_spec_service=None, config_port=None, logger=MagicMock())

    capacity = builder._calculate_capacity_distribution(_template(), 4)

    assert capacity["on_demand_count"] == 0
    assert capacity["spot_count"] == 4


def test_spot_fleet_legacy_config_without_expiry_or_request_count():
    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = ""
    builder = SpotFleetConfigBuilder(
        native_spec_service=None, config_port=config_port, logger=MagicMock()
    )
    template = _template(fleet_role="arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-role")

    config = builder._build_legacy(template, _request(requested_count=0), "lt-1", "1")

    assert "ValidUntil" not in config
    assert config["TargetCapacity"] == 1


def test_spot_fleet_legacy_config_with_expiry_sets_valid_until():
    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = ""
    builder = SpotFleetConfigBuilder(
        native_spec_service=None, config_port=config_port, logger=MagicMock()
    )
    template = _template(
        fleet_role="arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-role",
        spot_fleet_request_expiry=30,
    )

    config = builder._build_legacy(template, _request(), "lt-1", "1")

    assert "ValidUntil" in config


def test_launch_template_data_with_default_optional_fields():
    manager = AWSLaunchTemplateManager(
        aws_client=MagicMock(), logger=MagicMock(), config_port=MagicMock()
    )

    data = manager._create_launch_template_data_legacy(_template(), _request())

    assert "Monitoring" not in data
    assert "BlockDeviceMappings" not in data


def test_launch_template_data_keeps_disk_settings():
    manager = AWSLaunchTemplateManager(
        aws_client=MagicMock(), logger=MagicMock(), config_port=MagicMock()
    )
    template = _template(
        machine_disk_size_gb=50, machine_disk_type="gp3", iops=4000, monitoring_enabled=False
    )

    data = manager._create_launch_template_data_legacy(template, _request())

    assert data["Monitoring"] == {"Enabled": False}
    assert data["BlockDeviceMappings"][0]["Ebs"] == {
        "VolumeSize": 50,
        "VolumeType": "gp3",
        "DeleteOnTermination": True,
        "Iops": 4000,
    }


def test_launch_template_data_without_iops_has_no_iops_entry():
    manager = AWSLaunchTemplateManager(
        aws_client=MagicMock(), logger=MagicMock(), config_port=MagicMock()
    )
    template = _template(machine_disk_size_gb=50, machine_disk_type="gp3")

    data = manager._create_launch_template_data_legacy(template, _request())

    assert "Iops" not in data["BlockDeviceMappings"][0]["Ebs"]


def _handler() -> SpotFleetHandler:
    return SpotFleetHandler(
        aws_client=MagicMock(),
        logger=MagicMock(),
        aws_ops=MagicMock(),
        launch_template_manager=MagicMock(),
    )


def test_resolve_provider_api_falls_back_to_handler_default():
    request = _request()

    assert request.metadata == {}
    assert request.provider_api is None
    assert _handler()._resolve_provider_api(request, _template()) == "SpotFleet"


def test_resolve_provider_api_prefers_request_metadata_then_field():
    handler = _handler()

    assert handler._resolve_provider_api(_request(metadata={"provider_api": "M"})) == "M"
    assert handler._resolve_provider_api(_request(provider_api="F")) == "F"


def test_microvm_checks_apply_to_base_template_provider_api():
    adapter = AWSTemplateAdapter(
        template_config_manager=MagicMock(), aws_client=MagicMock(), logger=MagicMock()
    )
    microvm = Template(
        template_id="mv",
        provider_api="MicroVM",
        machine_image="arn:aws:lambda:eu-west-1:123456789012:image/x",
    )
    plain = Template(template_id="plain", machine_image="not-an-ami")

    assert "image_id" not in adapter.validate_field_values(microvm)
    assert "image_id" in adapter.validate_field_values(plain)


def _version_lt_data(**template_overrides: Any) -> dict[str, Any]:
    """Run _create_new_lt_version and return the LaunchTemplateData it sent."""
    aws_client = MagicMock()
    aws_client.get_selected_aws_provider_config.return_value = None
    aws_client.ec2_client.create_launch_template_version.return_value = {
        "LaunchTemplateVersion": {"VersionNumber": 2, "LaunchTemplateName": "user-lt"}
    }
    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = ""
    manager = AWSLaunchTemplateManager(
        aws_client=aws_client, logger=MagicMock(), config_port=config_port
    )
    template = _template(launch_template_id="lt-user", **template_overrides)

    manager._create_new_lt_version(template, _request())

    kwargs = aws_client.ec2_client.create_launch_template_version.call_args.kwargs
    return kwargs["LaunchTemplateData"]


def test_new_version_without_overrides_sends_no_monitoring_or_disk_settings():
    lt_data = _version_lt_data()

    assert "Monitoring" not in lt_data
    assert "BlockDeviceMappings" not in lt_data


def test_new_version_keeps_monitoring_disabled():
    lt_data = _version_lt_data(monitoring_enabled=False)

    assert lt_data["Monitoring"] == {"Enabled": False}


def test_new_version_sends_monitoring_enabled():
    assert _version_lt_data(monitoring_enabled=True)["Monitoring"] == {"Enabled": True}


def test_new_version_disk_with_iops():
    lt_data = _version_lt_data(machine_disk_size_gb=80, machine_disk_type="gp3", iops=5000)

    assert lt_data["BlockDeviceMappings"] == [
        {
            "DeviceName": "/dev/xvda",
            "Ebs": {
                "VolumeSize": 80,
                "VolumeType": "gp3",
                "DeleteOnTermination": True,
                "Iops": 5000,
            },
        }
    ]


def test_new_version_disk_without_iops():
    ebs = _version_lt_data(machine_disk_size_gb=80, machine_disk_type="gp3")["BlockDeviceMappings"][
        0
    ]["Ebs"]

    assert ebs == {"VolumeSize": 80, "VolumeType": "gp3", "DeleteOnTermination": True}


def test_new_version_ignores_iops_for_volume_types_without_provisioned_iops():
    ebs = _version_lt_data(machine_disk_size_gb=80, machine_disk_type="gp2", iops=5000)[
        "BlockDeviceMappings"
    ][0]["Ebs"]

    assert "Iops" not in ebs
