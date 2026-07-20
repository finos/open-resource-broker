"""Unit tests for AWSInfrastructureDiscoveryService.

Covers discover_vpcs, discover_subnets, discover_security_groups,
_summarize_sg_rules, _get_name_tag, _discover_spotfleet_role,
discover_infrastructure, and _discover_infrastructure_summary.

All AWS client calls are replaced with MagicMock — no real connections.
"""

from unittest.mock import MagicMock, patch

import pytest

from orb.providers.aws.services.infrastructure_discovery_service import (
    AWSInfrastructureDiscoveryService,
    SecurityGroupInfo,
    SubnetInfo,
    VPCInfo,
)

# ---------------------------------------------------------------------------
# Fixture / helper
# ---------------------------------------------------------------------------


def _make_service():
    """Build AWSInfrastructureDiscoveryService with mocked boto3 clients."""
    with patch("orb.providers.aws.session_factory.AWSSessionFactory.create_session") as mk:
        mock_session = MagicMock()
        mk.return_value = mock_session
        mock_session.client.return_value = MagicMock()
        svc = AWSInfrastructureDiscoveryService(
            region="us-east-1",
            profile=None,
            logger=MagicMock(),
            console=MagicMock(),
        )
    return svc


# ---------------------------------------------------------------------------
# VPCInfo / SubnetInfo / SecurityGroupInfo str representations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDataclassStrRepresentations:
    def test_vpc_info_str_default(self):
        vpc = VPCInfo(id="vpc-001", name="my-vpc", cidr_block="10.0.0.0/16", is_default=False)
        s = str(vpc)
        assert "vpc-001" in s
        assert "my-vpc" in s
        assert "10.0.0.0/16" in s
        assert "default" not in s

    def test_vpc_info_str_default_flag(self):
        vpc = VPCInfo(id="vpc-001", name="default", cidr_block="172.31.0.0/16", is_default=True)
        assert "(default)" in str(vpc)

    def test_subnet_info_str_public(self):
        subnet = SubnetInfo(
            id="subnet-001",
            name="pub",
            vpc_id="vpc-001",
            availability_zone="us-east-1a",
            cidr_block="10.0.1.0/24",
            is_public=True,
        )
        s = str(subnet)
        assert "subnet-001" in s
        assert "public" in s

    def test_subnet_info_str_private(self):
        subnet = SubnetInfo(
            id="subnet-002",
            name="priv",
            vpc_id="vpc-001",
            availability_zone="us-east-1b",
            cidr_block="10.0.2.0/24",
            is_public=False,
        )
        assert "private" in str(subnet)

    def test_security_group_info_str(self):
        sg = SecurityGroupInfo(
            id="sg-001",
            name="web-sg",
            description="Web layer",
            vpc_id="vpc-001",
            rule_summary="HTTP, HTTPS",
        )
        s = str(sg)
        assert "sg-001" in s
        assert "web-sg" in s


# ---------------------------------------------------------------------------
# _get_name_tag
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNameTag:
    def test_returns_name_value_when_present(self):
        svc = _make_service()
        tags = [{"Key": "Name", "Value": "my-vpc"}, {"Key": "Env", "Value": "prod"}]
        assert svc._get_name_tag(tags) == "my-vpc"

    def test_returns_none_when_no_name_tag(self):
        svc = _make_service()
        tags = [{"Key": "Env", "Value": "prod"}]
        assert svc._get_name_tag(tags) is None

    def test_returns_none_for_empty_list(self):
        svc = _make_service()
        assert svc._get_name_tag([]) is None


# ---------------------------------------------------------------------------
# _summarize_sg_rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSummarizeSgRules:
    def _make_sg(self, ip_permissions):
        return {"IpPermissions": ip_permissions}

    def test_no_rules_returns_no_inbound_rules(self):
        svc = _make_service()
        sg = self._make_sg([])
        assert svc._summarize_sg_rules(sg) == "No inbound rules"

    def test_http_port_80_classified(self):
        svc = _make_service()
        sg = self._make_sg([{"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80}])
        summary = svc._summarize_sg_rules(sg)
        assert "HTTP" in summary

    def test_https_port_443_classified(self):
        svc = _make_service()
        sg = self._make_sg([{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443}])
        assert "HTTPS" in svc._summarize_sg_rules(sg)

    def test_ssh_port_22_classified(self):
        svc = _make_service()
        sg = self._make_sg([{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22}])
        assert "SSH" in svc._summarize_sg_rules(sg)

    def test_all_traffic_protocol_minus_one(self):
        svc = _make_service()
        sg = self._make_sg([{"IpProtocol": "-1"}])
        assert "All traffic" in svc._summarize_sg_rules(sg)

    def test_custom_tcp_port_shown(self):
        svc = _make_service()
        sg = self._make_sg([{"IpProtocol": "tcp", "FromPort": 8080, "ToPort": 8080}])
        assert "TCP:8080" in svc._summarize_sg_rules(sg)

    def test_udp_protocol_uppercased(self):
        svc = _make_service()
        sg = self._make_sg([{"IpProtocol": "udp", "FromPort": 53, "ToPort": 53}])
        assert "UDP" in svc._summarize_sg_rules(sg)

    def test_multiple_rules_sorted(self):
        svc = _make_service()
        sg = self._make_sg(
            [
                {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443},
                {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80},
            ]
        )
        summary = svc._summarize_sg_rules(sg)
        # Both should be present and sorted
        assert "HTTP" in summary and "HTTPS" in summary
        assert summary.index("HTTP") < summary.index("HTTPS")


# ---------------------------------------------------------------------------
# discover_vpcs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoverVpcs:
    def test_returns_vpc_list(self):
        svc = _make_service()
        svc.ec2_client.describe_vpcs.return_value = {
            "Vpcs": [
                {
                    "VpcId": "vpc-001",
                    "CidrBlock": "10.0.0.0/16",
                    "IsDefault": False,
                    "Tags": [{"Key": "Name", "Value": "my-vpc"}],
                }
            ]
        }
        vpcs = svc.discover_vpcs()
        assert len(vpcs) == 1
        assert vpcs[0].id == "vpc-001"
        assert vpcs[0].name == "my-vpc"

    def test_vpc_without_name_tag_falls_back_to_id(self):
        svc = _make_service()
        svc.ec2_client.describe_vpcs.return_value = {
            "Vpcs": [
                {"VpcId": "vpc-002", "CidrBlock": "10.1.0.0/16", "IsDefault": False, "Tags": []}
            ]
        }
        vpcs = svc.discover_vpcs()
        assert vpcs[0].name == "vpc-002"

    def test_default_vpc_sorted_first(self):
        svc = _make_service()
        svc.ec2_client.describe_vpcs.return_value = {
            "Vpcs": [
                {"VpcId": "vpc-A", "CidrBlock": "10.0.0.0/16", "IsDefault": False, "Tags": []},
                {"VpcId": "vpc-B", "CidrBlock": "172.31.0.0/16", "IsDefault": True, "Tags": []},
            ]
        }
        vpcs = svc.discover_vpcs()
        assert vpcs[0].is_default is True

    def test_returns_empty_list_on_exception(self):
        svc = _make_service()
        svc.ec2_client.describe_vpcs.side_effect = RuntimeError("API error")
        assert svc.discover_vpcs() == []


# ---------------------------------------------------------------------------
# discover_subnets
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoverSubnets:
    def _setup(self, svc, subnets, route_tables=None):
        svc.ec2_client.describe_subnets.return_value = {"Subnets": subnets}
        svc.ec2_client.describe_route_tables.return_value = {"RouteTables": route_tables or []}

    def test_returns_subnet_list(self):
        svc = _make_service()
        self._setup(
            svc,
            [
                {
                    "SubnetId": "subnet-001",
                    "VpcId": "vpc-001",
                    "AvailabilityZone": "us-east-1a",
                    "CidrBlock": "10.0.1.0/24",
                    "Tags": [{"Key": "Name", "Value": "public-sub"}],
                }
            ],
        )
        subnets = svc.discover_subnets("vpc-001")
        assert len(subnets) == 1
        assert subnets[0].id == "subnet-001"
        assert subnets[0].name == "public-sub"

    def test_subnet_is_public_when_igw_route_present(self):
        svc = _make_service()
        route_tables = [
            {
                "Routes": [{"GatewayId": "igw-001"}],
                "Associations": [{"SubnetId": "subnet-001"}],
            }
        ]
        self._setup(
            svc,
            [
                {
                    "SubnetId": "subnet-001",
                    "VpcId": "vpc-001",
                    "AvailabilityZone": "us-east-1a",
                    "CidrBlock": "10.0.1.0/24",
                    "Tags": [],
                }
            ],
            route_tables=route_tables,
        )
        subnets = svc.discover_subnets("vpc-001")
        assert subnets[0].is_public is True

    def test_subnet_is_private_when_no_igw_route(self):
        svc = _make_service()
        route_tables = [
            {
                "Routes": [{"GatewayId": "vgw-001"}],
                "Associations": [{"SubnetId": "subnet-001"}],
            }
        ]
        self._setup(
            svc,
            [
                {
                    "SubnetId": "subnet-001",
                    "VpcId": "vpc-001",
                    "AvailabilityZone": "us-east-1a",
                    "CidrBlock": "10.0.2.0/24",
                    "Tags": [],
                }
            ],
            route_tables=route_tables,
        )
        subnets = svc.discover_subnets("vpc-001")
        assert subnets[0].is_public is False

    def test_returns_empty_on_exception(self):
        svc = _make_service()
        svc.ec2_client.describe_subnets.side_effect = RuntimeError("boom")
        assert svc.discover_subnets("vpc-001") == []

    def test_subnet_without_name_tag_uses_id(self):
        svc = _make_service()
        self._setup(
            svc,
            [
                {
                    "SubnetId": "subnet-999",
                    "VpcId": "vpc-001",
                    "AvailabilityZone": "us-east-1c",
                    "CidrBlock": "10.0.9.0/24",
                    "Tags": [],
                }
            ],
        )
        subnets = svc.discover_subnets("vpc-001")
        assert subnets[0].name == "subnet-999"


# ---------------------------------------------------------------------------
# discover_security_groups
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoverSecurityGroups:
    def test_returns_sg_list(self):
        svc = _make_service()
        svc.ec2_client.describe_security_groups.return_value = {
            "SecurityGroups": [
                {
                    "GroupId": "sg-001",
                    "GroupName": "web-sg",
                    "Description": "Web layer",
                    "VpcId": "vpc-001",
                    "IpPermissions": [],
                }
            ]
        }
        sgs = svc.discover_security_groups("vpc-001")
        assert len(sgs) == 1
        assert sgs[0].id == "sg-001"

    def test_returns_empty_on_exception(self):
        svc = _make_service()
        svc.ec2_client.describe_security_groups.side_effect = RuntimeError("sg err")
        assert svc.discover_security_groups("vpc-001") == []

    def test_sorted_by_name(self):
        svc = _make_service()
        svc.ec2_client.describe_security_groups.return_value = {
            "SecurityGroups": [
                {
                    "GroupId": "sg-B",
                    "GroupName": "z-sg",
                    "Description": "",
                    "VpcId": "vpc-001",
                    "IpPermissions": [],
                },
                {
                    "GroupId": "sg-A",
                    "GroupName": "a-sg",
                    "Description": "",
                    "VpcId": "vpc-001",
                    "IpPermissions": [],
                },
            ]
        }
        sgs = svc.discover_security_groups("vpc-001")
        assert sgs[0].name == "a-sg"


# ---------------------------------------------------------------------------
# _discover_spotfleet_role
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoverSpotfleetRole:
    def test_returns_arn_on_success(self):
        svc = _make_service()
        svc.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
        svc.iam_client.get_role.return_value = {}
        arn = svc._discover_spotfleet_role()
        assert arn is not None
        assert "123456789012" in arn
        assert "AWSServiceRoleForEC2SpotFleet" in arn

    def test_returns_arn_even_when_iam_check_fails(self):
        svc = _make_service()
        svc.sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
        svc.iam_client.get_role.side_effect = RuntimeError("not found")
        arn = svc._discover_spotfleet_role()
        assert arn is not None

    def test_returns_none_when_sts_fails(self):
        svc = _make_service()
        svc.sts_client.get_caller_identity.side_effect = RuntimeError("sts down")
        assert svc._discover_spotfleet_role() is None


# ---------------------------------------------------------------------------
# discover_infrastructure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoverInfrastructure:
    def _setup_vpcs(self, svc, vpcs=None):
        vpcs = vpcs or [
            {
                "VpcId": "vpc-001",
                "CidrBlock": "10.0.0.0/16",
                "IsDefault": False,
                "Tags": [{"Key": "Name", "Value": "test-vpc"}],
            }
        ]
        svc.ec2_client.describe_vpcs.return_value = {"Vpcs": vpcs}
        svc.ec2_client.describe_subnets.return_value = {"Subnets": []}
        svc.ec2_client.describe_route_tables.return_value = {"RouteTables": []}
        svc.ec2_client.describe_security_groups.return_value = {"SecurityGroups": []}

    def test_returns_provider_name_in_result(self):
        svc = _make_service()
        self._setup_vpcs(svc)
        result = svc.discover_infrastructure({"name": "my-provider", "config": {}})
        assert result["provider"] == "my-provider"

    def test_returns_zero_vpcs_when_none_found(self):
        svc = _make_service()
        svc.ec2_client.describe_vpcs.return_value = {"Vpcs": []}
        result = svc.discover_infrastructure({"name": "p", "config": {}})
        assert result["vpcs"] == 0

    def test_returns_vpc_count(self):
        svc = _make_service()
        self._setup_vpcs(svc)
        result = svc.discover_infrastructure({"name": "p", "config": {}})
        assert result["vpcs"] == 1

    def test_show_filter_empty_string_returns_error(self):
        svc = _make_service()
        self._setup_vpcs(svc)
        cli_args = MagicMock()
        cli_args.summary = False
        cli_args.show = "   "  # blank show
        cli_args.all = False
        result = svc.discover_infrastructure({"name": "p", "config": {}, "cli_args": cli_args})
        assert "error" in result

    def test_show_all_flag_sets_show_all(self):
        svc = _make_service()
        self._setup_vpcs(svc)
        cli_args = MagicMock()
        cli_args.summary = False
        cli_args.show = None
        cli_args.all = True
        result = svc.discover_infrastructure({"name": "p", "config": {}, "cli_args": cli_args})
        assert "vpcs" in result

    def test_exception_returns_error_dict(self):
        svc = _make_service()
        # discover_vpcs catches EC2 errors internally; to trigger the outer
        # exception handler we must raise from discover_vpcs itself
        svc.discover_vpcs = MagicMock(side_effect=RuntimeError("total fail"))
        result = svc.discover_infrastructure({"name": "p", "config": {}})
        assert "error" in result

    def test_summary_flag_returns_counts(self):
        svc = _make_service()
        self._setup_vpcs(svc)
        cli_args = MagicMock()
        cli_args.summary = True
        result = svc.discover_infrastructure({"name": "p", "config": {}, "cli_args": cli_args})
        assert "vpcs" in result

    def test_show_filter_sg_alias(self):
        svc = _make_service()
        self._setup_vpcs(svc)
        cli_args = MagicMock()
        cli_args.summary = False
        cli_args.show = "sg"
        cli_args.all = False
        result = svc.discover_infrastructure({"name": "p", "config": {}, "cli_args": cli_args})
        assert "vpcs" in result

    def test_show_filter_all_keyword(self):
        svc = _make_service()
        self._setup_vpcs(svc)
        cli_args = MagicMock()
        cli_args.summary = False
        cli_args.show = "all"
        cli_args.all = False
        result = svc.discover_infrastructure({"name": "p", "config": {}, "cli_args": cli_args})
        assert "vpcs" in result
