"""Guard tests for TagSpecifications ResourceType per AWS API.

AWS enforces strict rules on which ResourceType values are valid in
TagSpecifications for each fleet API:

- RequestSpotFleet: only "spot-fleet-request"
- CreateFleet (request/maintain): only "fleet"
- CreateFleet (instant): "fleet" and "instance"
- RunInstances: "instance" (and optionally "spot-instances-request")

These tests exist because commit 3fe7ad41 silently reintroduced invalid
"instance" ResourceType entries that had been explicitly removed in
ec0a63e3 (SpotFleet) and 9b3aae9f (EC2Fleet), causing 22 onaws test
failures with InvalidTagKey.Malformed.
"""

from unittest.mock import Mock

import pytest

from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.aggregate import Request
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.domain.template.value_objects import AWSFleetType
from orb.providers.aws.infrastructure.handlers.ec2_fleet.config_builder import EC2FleetConfigBuilder
from orb.providers.aws.infrastructure.handlers.spot_fleet.config_builder import (
    SpotFleetConfigBuilder,
)


@pytest.fixture
def mock_logger():
    return Mock()


@pytest.fixture
def mock_config_port():
    port = Mock(spec=ConfigurationPort)
    port.get_resource_prefix.return_value = "orb-"
    return port


@pytest.fixture
def mock_template():
    t = Mock(spec=AWSTemplate)
    t.template_id = "tpl-test"
    t.image_id = "ami-12345678"
    t.machine_types = {"t3.medium": 1}
    t.machine_types_ondemand = {}
    t.subnet_ids = ["subnet-abc"]
    t.security_group_ids = ["sg-abc"]
    t.tags = {"Environment": "test"}
    t.fleet_role = "arn:aws:iam::123456789012:role/fleet-role"
    t.price_type = "spot"
    t.allocation_strategy = "lowestPrice"
    t.allocation_strategy_on_demand = None
    t.max_price = None
    t.percent_on_demand = None
    t.context = None
    t.abis_instance_requirements = None
    t.get_instance_requirements_payload.return_value = None
    t.get_spot_fleet_allocation_strategy.return_value = "lowestPrice"
    t.get_ec2_fleet_allocation_strategy.return_value = "lowest-price"
    t.get_ec2_fleet_on_demand_allocation_strategy.return_value = "lowest-price"
    t.spot_fleet_request_expiry = None
    return t


@pytest.fixture
def mock_request():
    r = Mock(spec=Request)
    r.request_id = "req-test-001"
    r.requested_count = 2
    return r


def _extract_resource_types(config: dict) -> set[str]:
    """Extract all ResourceType values from TagSpecifications."""
    tag_specs = config.get("TagSpecifications", [])
    return {spec["ResourceType"] for spec in tag_specs}


class TestSpotFleetTagSpecifications:
    """SpotFleet RequestSpotFleet only accepts ResourceType=spot-fleet-request."""

    VALID_RESOURCE_TYPES = {"spot-fleet-request"}

    @pytest.mark.parametrize("fleet_type", ["request", "maintain"])
    def test_no_instance_resource_type(
        self, mock_logger, mock_config_port, mock_template, mock_request, fleet_type
    ):
        mock_template.fleet_type = fleet_type
        builder = SpotFleetConfigBuilder(
            native_spec_service=None,
            config_port=mock_config_port,
            logger=mock_logger,
        )
        config = builder.build(mock_template, mock_request, "lt-123", "$Latest")
        resource_types = _extract_resource_types(config)

        assert "instance" not in resource_types, (
            f"SpotFleet TagSpecifications must not contain ResourceType='instance' "
            f"(fleet_type={fleet_type}). AWS rejects it with InvalidTagKey.Malformed. "
            f"Found: {resource_types}"
        )
        assert resource_types <= self.VALID_RESOURCE_TYPES, (
            f"SpotFleet TagSpecifications contains invalid ResourceType(s): "
            f"{resource_types - self.VALID_RESOURCE_TYPES}"
        )


class TestEC2FleetTagSpecifications:
    """EC2Fleet CreateFleet accepts ResourceType=instance only for instant fleets."""

    def test_request_fleet_no_instance_tag(
        self, mock_logger, mock_config_port, mock_template, mock_request
    ):
        mock_template.fleet_type = AWSFleetType.REQUEST
        builder = EC2FleetConfigBuilder(
            native_spec_service=None,
            config_port=mock_config_port,
            logger=mock_logger,
        )
        config = builder.build(mock_template, mock_request, "lt-123", "$Latest")
        resource_types = _extract_resource_types(config)

        assert "instance" not in resource_types, (
            "EC2Fleet request-type must not tag ResourceType='instance'. "
            "AWS rejects it with InvalidTagKey.Malformed."
        )
        assert "fleet" in resource_types

    def test_maintain_fleet_no_instance_tag(
        self, mock_logger, mock_config_port, mock_template, mock_request
    ):
        mock_template.fleet_type = AWSFleetType.MAINTAIN
        builder = EC2FleetConfigBuilder(
            native_spec_service=None,
            config_port=mock_config_port,
            logger=mock_logger,
        )
        config = builder.build(mock_template, mock_request, "lt-123", "$Latest")
        resource_types = _extract_resource_types(config)

        assert "instance" not in resource_types, (
            "EC2Fleet maintain-type must not tag ResourceType='instance'. "
            "AWS rejects it with InvalidTagKey.Malformed."
        )
        assert "fleet" in resource_types

    def test_instant_fleet_allows_instance_tag(
        self, mock_logger, mock_config_port, mock_template, mock_request
    ):
        mock_template.fleet_type = AWSFleetType.INSTANT
        builder = EC2FleetConfigBuilder(
            native_spec_service=None,
            config_port=mock_config_port,
            logger=mock_logger,
        )
        config = builder.build(mock_template, mock_request, "lt-123", "$Latest")
        resource_types = _extract_resource_types(config)

        assert "fleet" in resource_types
        assert "instance" in resource_types, (
            "EC2Fleet instant-type should include ResourceType='instance' "
            "so launched instances get tagged."
        )
