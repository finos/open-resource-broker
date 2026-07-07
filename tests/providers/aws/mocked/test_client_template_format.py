"""Tests for the client template format: launchTemplateId-based EC2Fleet spot provisioning.

Covers the real-world template shape from customer deployments:
- launchTemplateId set, NO imageId
- No providerApi (defaults to EC2Fleet via HF scheduler)
- vmTypes with multiple instance types
- priceType: spot, percentOnDemand: 0
- allocationStrategy: capacityOptimized
- fleetRole set
- subnetId as comma-separated string
"""

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate

REGION = "eu-west-2"

FAKE_FLEET_ROLE = (
    "arn:aws:iam::123456789012:role/aws-service-role/"
    "spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
)

# Mirrors the real customer template shape with all IDs obfuscated
_CLIENT_HF_TEMPLATE: dict[str, Any] = {
    "templateId": "Template-VM-CLIENT",
    "maxNumber": 500,
    "launchTemplateId": "lt-12345678abcdef012",
    "subnetId": "subnet-11111111,subnet-22222222,subnet-33333333",
    "fleetRole": FAKE_FLEET_ROLE,
    "percentOnDemand": 0,
    "poolsCount": 48,
    "priceType": "spot",
    "spotFleetRequestExpiry": 40,
    "allocationStrategy": "capacityOptimized",
    "vmTypes": {
        "r5a.large": 1,
        "r5.large": 1,
        "r6a.large": 1,
        "r6i.large": 1,
        "r5a.xlarge": 2,
        "r5.xlarge": 2,
    },
    "vmTypesPriority": {
        "r5a.large": 1,
        "r5.large": 1,
        "r6a.large": 1,
        "r6i.large": 1,
        "r5a.xlarge": 2,
        "r5.xlarge": 2,
    },
    "instanceTags": {"Environment": "test", "ManagedBy": "orb"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_hf_file(path: Path, templates: list[dict]) -> None:
    path.write_text(json.dumps({"scheduler_type": "hostfactory", "templates": templates}))


# ---------------------------------------------------------------------------
# Task A-1: HF scheduler loads the template without error
# ---------------------------------------------------------------------------


class TestClientTemplateHFLoading:
    def test_hf_strategy_loads_client_template_without_error(self, tmp_path):
        """HF strategy parses the client template shape without raising."""
        from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        tpl_file = tmp_path / "aws_templates.json"
        _write_hf_file(tpl_file, [_CLIENT_HF_TEMPLATE])

        strategy = HostFactorySchedulerStrategy()
        results = strategy.load_templates_from_path(str(tpl_file))

        assert len(results) == 1

    def test_hf_strategy_maps_launch_template_id(self, tmp_path):
        """launchTemplateId -> launch_template_id after HF field mapping."""
        from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        tpl_file = tmp_path / "aws_templates.json"
        _write_hf_file(tpl_file, [_CLIENT_HF_TEMPLATE])

        strategy = HostFactorySchedulerStrategy()
        results = strategy.load_templates_from_path(str(tpl_file))

        assert results[0].get("launch_template_id") == "lt-12345678abcdef012"

    def test_hf_strategy_maps_subnet_id_string_to_list(self, tmp_path):
        """subnetId comma-separated string -> subnet_ids list with all three entries."""
        from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        tpl_file = tmp_path / "aws_templates.json"
        _write_hf_file(tpl_file, [_CLIENT_HF_TEMPLATE])

        strategy = HostFactorySchedulerStrategy()
        results = strategy.load_templates_from_path(str(tpl_file))

        subnet_ids = results[0].get("subnet_ids", [])
        assert "subnet-11111111" in subnet_ids
        assert "subnet-22222222" in subnet_ids
        assert "subnet-33333333" in subnet_ids

    def test_hf_strategy_maps_percent_on_demand_zero(self, tmp_path):
        """percentOnDemand: 0 -> percent_on_demand: 0 (not None, not missing)."""
        from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        tpl_file = tmp_path / "aws_templates.json"
        _write_hf_file(tpl_file, [_CLIENT_HF_TEMPLATE])

        strategy = HostFactorySchedulerStrategy()
        results = strategy.load_templates_from_path(str(tpl_file))

        assert results[0].get("percent_on_demand") == 0

    def test_hf_strategy_maps_multiple_vm_types(self, tmp_path):
        """vmTypes dict with 6 instance types -> machine_types with all entries."""
        from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        tpl_file = tmp_path / "aws_templates.json"
        _write_hf_file(tpl_file, [_CLIENT_HF_TEMPLATE])

        strategy = HostFactorySchedulerStrategy()
        results = strategy.load_templates_from_path(str(tpl_file))

        machine_types = results[0].get("machine_types", {})
        assert len(machine_types) == 6
        assert "r5a.large" in machine_types
        assert "r6i.xlarge" not in machine_types  # not in our trimmed set

    def test_hf_strategy_no_provider_api_in_template(self, tmp_path):
        """Template with no providerApi field loads without KeyError."""
        from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        assert "providerApi" not in _CLIENT_HF_TEMPLATE

        tpl_file = tmp_path / "aws_templates.json"
        _write_hf_file(tpl_file, [_CLIENT_HF_TEMPLATE])

        strategy = HostFactorySchedulerStrategy()
        # Must not raise
        results = strategy.load_templates_from_path(str(tpl_file))
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Task A-2: AWSTemplate domain object accepts launchTemplateId without imageId
# ---------------------------------------------------------------------------


class TestAWSTemplateLaunchTemplateIdWithoutImageId:
    def test_aws_template_accepts_launch_template_id_without_image_id(self, moto_vpc_resources):
        """AWSTemplate can be constructed with launch_template_id and no image_id."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-lt-only",
            name="lt-only",
            provider_api="EC2Fleet",
            machine_types={"r5a.large": 1, "r5.large": 1},
            image_id=None,
            launch_template_id="lt-12345678abcdef012",
            max_instances=10,
            price_type="spot",
            percent_on_demand=0,
            allocation_strategy="capacityOptimized",
            fleet_role=FAKE_FLEET_ROLE,
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
            tags={"Environment": "test"},
        )

        assert template.launch_template_id == "lt-12345678abcdef012"
        assert template.image_id is None

    def test_aws_template_image_id_not_required_when_launch_template_id_set(
        self, moto_vpc_resources
    ):
        """image_id is optional when launch_template_id is provided — no ValidationError."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        # Should not raise
        template = AWSTemplate(
            template_id="tpl-lt-no-ami",
            name="lt-no-ami",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            max_instances=5,
            price_type="spot",
            launch_template_id="lt-12345678abcdef012",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        assert template.image_id is None
        assert template.launch_template_id == "lt-12345678abcdef012"

    def test_aws_template_provider_api_defaults_to_ec2fleet_when_absent(self, moto_vpc_resources):
        """When provider_api is None, the template still constructs; EC2Fleet is the expected default."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-no-api",
            name="no-api",
            machine_types={"r5.large": 1},
            max_instances=5,
            price_type="spot",
            launch_template_id="lt-12345678abcdef012",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        # provider_api is None — caller must set it from config default (EC2Fleet)
        assert template.provider_api is None
        assert template.launch_template_id == "lt-12345678abcdef012"

    def test_aws_template_allocation_strategy_capacity_optimized(self, moto_vpc_resources):
        """capacityOptimized allocation strategy is accepted and normalised."""
        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]

        template = AWSTemplate(
            template_id="tpl-cap-opt",
            name="cap-opt",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            max_instances=5,
            price_type="spot",
            allocation_strategy="capacityOptimized",
            launch_template_id="lt-12345678abcdef012",
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        ec2_strategy = template.get_ec2_fleet_allocation_strategy()
        # EC2 Fleet API uses "capacity-optimized"
        assert "capacity" in ec2_strategy.lower()


# ---------------------------------------------------------------------------
# Task A-3: EC2Fleet spot provisioning with launchTemplateId (moto-backed)
# ---------------------------------------------------------------------------


class TestEC2FleetSpotWithLaunchTemplateId:
    """Verify EC2Fleet spot fleet is created when template has launch_template_id."""

    @pytest.fixture
    def aws_client(self, moto_aws):
        from orb.providers.aws.infrastructure.aws_client import AWSClient

        client = MagicMock(spec=AWSClient)
        client.ec2_client = boto3.client("ec2", region_name=REGION)
        client.autoscaling_client = boto3.client("autoscaling", region_name=REGION)
        client.sts_client = boto3.client("sts", region_name=REGION)
        client.ssm_client = boto3.client("ssm", region_name=REGION)
        return client

    @pytest.fixture
    def ec2_client(self, moto_aws):
        return boto3.client("ec2", region_name=REGION)

    def _make_logger(self) -> Any:
        logger = MagicMock()
        logger.debug = MagicMock()
        logger.info = MagicMock()
        logger.warning = MagicMock()
        logger.error = MagicMock()
        return logger

    def _make_config_port(self) -> Any:
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

    def _make_lt_manager(self, aws_client: Any, logger: Any) -> Any:
        """LT manager that uses the existing launch_template_id from the template."""
        from orb.providers.aws.infrastructure.launch_template.manager import (
            AWSLaunchTemplateManager,
            LaunchTemplateResult,
        )

        lt_manager = MagicMock(spec=AWSLaunchTemplateManager)

        def _create_or_update(template: AWSTemplate, request: Any) -> LaunchTemplateResult:
            # When launch_template_id is already set, return it directly
            if template.launch_template_id:
                # Register the pre-existing LT in moto so fleet creation succeeds
                lt_name = f"orb-existing-{template.launch_template_id}"
                try:
                    resp = aws_client.ec2_client.create_launch_template(
                        LaunchTemplateName=lt_name,
                        LaunchTemplateData={
                            "ImageId": "ami-12345678",
                            "InstanceType": next(iter(template.machine_types.keys())),
                        },
                    )
                    lt_id = resp["LaunchTemplate"]["LaunchTemplateId"]
                    version = str(resp["LaunchTemplate"]["LatestVersionNumber"])
                except Exception:
                    lt_id = template.launch_template_id
                    version = "1"
                return LaunchTemplateResult(
                    template_id=lt_id,
                    version=version,
                    template_name=lt_name,
                    is_new_template=False,
                )
            # Fallback: create a new one
            lt_name = f"orb-lt-{request.request_id}"
            resp = aws_client.ec2_client.create_launch_template(
                LaunchTemplateName=lt_name,
                LaunchTemplateData={
                    "ImageId": template.image_id or "ami-12345678",
                    "InstanceType": next(iter(template.machine_types.keys())),
                },
            )
            lt_id = resp["LaunchTemplate"]["LaunchTemplateId"]
            version = str(resp["LaunchTemplate"]["LatestVersionNumber"])
            return LaunchTemplateResult(
                template_id=lt_id,
                version=version,
                template_name=lt_name,
                is_new_template=True,
            )

        lt_manager.create_or_update_launch_template.side_effect = _create_or_update
        return lt_manager

    def _make_request(self, request_id: str = "req-lt-001", count: int = 2) -> Any:
        req = MagicMock()
        req.request_id = request_id
        req.requested_count = count
        req.template_id = "tpl-lt-only"
        req.metadata = {}
        req.resource_ids = []
        req.provider_data = {}
        req.provider_api = None
        return req

    def test_ec2fleet_spot_acquire_with_launch_template_id(
        self, aws_client, ec2_client, moto_vpc_resources
    ):
        """EC2Fleet spot acquire succeeds when template has launch_template_id and no imageId."""
        from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
        from orb.providers.aws.utilities.aws_operations import AWSOperations

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        logger = self._make_logger()
        config_port = self._make_config_port()
        lt_manager = self._make_lt_manager(aws_client, logger)
        aws_ops = AWSOperations(aws_client, logger, config_port)

        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=logger,
            aws_ops=aws_ops,
            launch_template_manager=lt_manager,
            config_port=config_port,
        )

        template = AWSTemplate(
            template_id="tpl-lt-spot",
            name="lt-spot",
            provider_api="EC2Fleet",
            machine_types={"r5a.large": 1, "r5.large": 1, "r6a.large": 1},
            image_id=None,
            launch_template_id="lt-12345678abcdef012",
            max_instances=50,
            price_type="spot",
            percent_on_demand=0,
            allocation_strategy="capacityOptimized",
            fleet_role=FAKE_FLEET_ROLE,
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
            tags={"Environment": "test"},
        )

        request = self._make_request(request_id="req-lt-spot-001", count=2)
        result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        fleet_id = result["resource_ids"][0]
        assert fleet_id.startswith("fleet-")

        resp = ec2_client.describe_fleets(FleetIds=[fleet_id])
        assert len(resp["Fleets"]) == 1

    def test_ec2fleet_spot_provider_data_resource_type(self, aws_client, moto_vpc_resources):
        """provider_data identifies resource as ec2_fleet for launch_template_id template."""
        from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
        from orb.providers.aws.utilities.aws_operations import AWSOperations

        subnet_id = moto_vpc_resources["subnet_ids"][0]
        sg_id = moto_vpc_resources["sg_id"]
        logger = self._make_logger()
        config_port = self._make_config_port()
        lt_manager = self._make_lt_manager(aws_client, logger)
        aws_ops = AWSOperations(aws_client, logger, config_port)

        handler = EC2FleetHandler(
            aws_client=aws_client,
            logger=logger,
            aws_ops=aws_ops,
            launch_template_manager=lt_manager,
            config_port=config_port,
        )

        template = AWSTemplate(
            template_id="tpl-lt-pd",
            name="lt-pd",
            provider_api="EC2Fleet",
            machine_types={"r5.large": 1},
            image_id=None,
            launch_template_id="lt-12345678abcdef012",
            max_instances=10,
            price_type="spot",
            percent_on_demand=0,
            fleet_role=FAKE_FLEET_ROLE,
            subnet_ids=[subnet_id],
            security_group_ids=[sg_id],
        )

        request = self._make_request(request_id="req-lt-pd-001", count=1)
        result = handler.acquire_hosts(request, template)

        assert result["provider_data"]["resource_type"] == "ec2_fleet"
