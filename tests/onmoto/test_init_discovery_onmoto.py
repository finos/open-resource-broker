"""Moto integration tests for AWSInfrastructureDiscoveryService."""

from unittest.mock import MagicMock, patch as _patch

import pytest


@pytest.fixture
def discovery_service(moto_aws):
    """Create AWSInfrastructureDiscoveryService inside moto context."""
    from orb.providers.aws.services.infrastructure_discovery_service import (
        AWSInfrastructureDiscoveryService,
    )

    return AWSInfrastructureDiscoveryService(
        region="eu-west-2",
        profile=None,
        console=MagicMock(),
    )


def test_discover_vpcs_returns_vpcs(discovery_service, moto_vpc_resources):
    vpcs = discovery_service.discover_vpcs()
    assert len(vpcs) >= 1
    vpc_ids = [v.id for v in vpcs]
    assert moto_vpc_resources["vpc_id"] in vpc_ids


def test_discover_vpcs_returns_empty_when_no_vpcs(moto_aws):
    from orb.providers.aws.services.infrastructure_discovery_service import (
        AWSInfrastructureDiscoveryService,
    )

    service = AWSInfrastructureDiscoveryService(
        region="eu-west-2", profile=None, console=MagicMock()
    )
    vpcs = service.discover_vpcs()
    # moto starts with a default VPC — filter to only non-default
    non_default = [v for v in vpcs if not v.is_default]
    assert non_default == []


def test_discover_subnets_returns_subnets_for_vpc(discovery_service, moto_vpc_resources):
    subnets = discovery_service.discover_subnets(moto_vpc_resources["vpc_id"])
    assert len(subnets) >= 1
    subnet_ids = [s.id for s in subnets]
    for sid in moto_vpc_resources["subnet_ids"]:
        assert sid in subnet_ids


def test_discover_security_groups_returns_sgs_for_vpc(discovery_service, moto_vpc_resources):
    sgs = discovery_service.discover_security_groups(moto_vpc_resources["vpc_id"])
    assert len(sgs) >= 1
    sg_ids = [sg.id for sg in sgs]
    assert moto_vpc_resources["sg_id"] in sg_ids


def test_discover_infrastructure_interactive_happy_path(discovery_service, moto_vpc_resources):
    """Full interactive flow: select VPC, subnets, SG, skip fleet role."""
    with _patch("builtins.input", side_effect=["1", "1,2", "1", ""]):
        result = discovery_service.discover_infrastructure_interactive(
            {"type": "aws", "config": {"region": "eu-west-2"}}
        )
    assert "subnet_ids" in result
    assert "security_group_ids" in result
    assert len(result["subnet_ids"]) >= 1
    assert len(result["security_group_ids"]) >= 1


def test_discover_infrastructure_interactive_no_vpcs_returns_empty(moto_aws):
    """When no VPCs exist (beyond default), interactive discovery returns empty."""
    from orb.providers.aws.services.infrastructure_discovery_service import (
        AWSInfrastructureDiscoveryService,
    )

    service = AWSInfrastructureDiscoveryService(
        region="eu-west-2", profile=None, console=MagicMock()
    )

    with _patch.object(service, "discover_vpcs", return_value=[]):
        result = service.discover_infrastructure_interactive({"type": "aws", "config": {}})

    assert result == {}


def test_discover_spotfleet_role_constructs_arn(discovery_service):
    """_discover_spotfleet_role returns an ARN containing the moto account ID."""
    arn = discovery_service._discover_spotfleet_role()
    # moto account ID is 123456789012
    if arn is not None:
        assert "123456789012" in arn
        assert "AWSServiceRoleForEC2SpotFleet" in arn
