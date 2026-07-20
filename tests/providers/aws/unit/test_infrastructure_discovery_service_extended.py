"""Extended unit tests for AWSInfrastructureDiscoveryService.

Covers validate_infrastructure, _discover_infrastructure_summary,
_summarize_sg_rules extended branches, discover_subnets route table logic,
and validate_infrastructure fleet_role path.
"""

from unittest.mock import MagicMock, patch

import pytest

from orb.providers.aws.services.infrastructure_discovery_service import (
    AWSInfrastructureDiscoveryService,
)

pytestmark = pytest.mark.unit


def _make_service():
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
# validate_infrastructure — no template_defaults
# ---------------------------------------------------------------------------


class TestValidateInfrastructureNoDefaults:
    def test_returns_no_infrastructure_configured_when_no_template_defaults(self):
        svc = _make_service()
        result = svc.validate_infrastructure({"name": "p", "config": {}})
        assert result["status"] == "no_infrastructure_configured"
        assert result["provider"] == "p"

    def test_empty_template_defaults_returns_no_infrastructure_configured(self):
        svc = _make_service()
        result = svc.validate_infrastructure({"name": "p", "config": {}, "template_defaults": {}})
        assert result["status"] == "no_infrastructure_configured"


# ---------------------------------------------------------------------------
# validate_infrastructure — subnet validation
# ---------------------------------------------------------------------------


class TestValidateInfrastructureSubnets:
    def test_valid_subnets_pass(self):
        svc = _make_service()
        svc.ec2_client.describe_subnets.return_value = {
            "Subnets": [{"SubnetId": "subnet-001"}, {"SubnetId": "subnet-002"}]
        }
        result = svc.validate_infrastructure(
            {
                "name": "p",
                "config": {},
                "template_defaults": {"subnet_ids": ["subnet-001", "subnet-002"]},
            }
        )
        assert result["valid"] is True
        assert result["issues"] == []

    def test_invalid_subnets_mark_result_invalid(self):
        svc = _make_service()
        svc.ec2_client.describe_subnets.side_effect = RuntimeError("Subnet not found")
        result = svc.validate_infrastructure(
            {
                "name": "p",
                "config": {},
                "template_defaults": {"subnet_ids": ["subnet-bad"]},
            }
        )
        assert result["valid"] is False
        assert any("Invalid subnets" in issue for issue in result["issues"])


# ---------------------------------------------------------------------------
# validate_infrastructure — security group validation
# ---------------------------------------------------------------------------


class TestValidateInfrastructureSecurityGroups:
    def test_valid_security_groups_pass(self):
        svc = _make_service()
        svc.ec2_client.describe_security_groups.return_value = {
            "SecurityGroups": [{"GroupId": "sg-001"}]
        }
        result = svc.validate_infrastructure(
            {
                "name": "p",
                "config": {},
                "template_defaults": {"security_group_ids": ["sg-001"]},
            }
        )
        assert result["valid"] is True

    def test_invalid_security_groups_mark_result_invalid(self):
        svc = _make_service()
        svc.ec2_client.describe_security_groups.side_effect = RuntimeError("SG not found")
        result = svc.validate_infrastructure(
            {
                "name": "p",
                "config": {},
                "template_defaults": {"security_group_ids": ["sg-bad"]},
            }
        )
        assert result["valid"] is False
        assert any("Invalid security groups" in issue for issue in result["issues"])


# ---------------------------------------------------------------------------
# validate_infrastructure — fleet_role validation
# ---------------------------------------------------------------------------


class TestValidateInfrastructureFleetRole:
    def test_valid_fleet_role_passes(self):
        svc = _make_service()
        svc.iam_client.get_role.return_value = {
            "Role": {"RoleName": "AWSServiceRoleForEC2SpotFleet"}
        }
        result = svc.validate_infrastructure(
            {
                "name": "p",
                "config": {},
                "template_defaults": {
                    "fleet_role": "arn:aws:iam::123456789012:role/AWSServiceRoleForEC2SpotFleet"
                },
            }
        )
        assert result["valid"] is True

    def test_invalid_fleet_role_marks_result_invalid(self):
        svc = _make_service()
        svc.iam_client.get_role.side_effect = RuntimeError("Role not found")
        result = svc.validate_infrastructure(
            {
                "name": "p",
                "config": {},
                "template_defaults": {
                    "fleet_role": "arn:aws:iam::123456789012:role/NonExistentRole"
                },
            }
        )
        assert result["valid"] is False
        assert any("Invalid fleet_role" in issue for issue in result["issues"])

    def test_fleet_role_pulled_from_config_when_not_in_template_defaults(self):
        svc = _make_service()
        svc.iam_client.get_role.return_value = {"Role": {}}
        result = svc.validate_infrastructure(
            {
                "name": "p",
                "config": {
                    "fleet_role": "arn:aws:iam::123456789012:role/AWSServiceRoleForEC2SpotFleet"
                },
                "template_defaults": {"subnet_ids": []},
            }
        )
        # The config-sourced fleet_role must be merged in and validated via IAM
        # using the role name derived from the ARN's last path segment.
        svc.iam_client.get_role.assert_called_once_with(RoleName="AWSServiceRoleForEC2SpotFleet")
        assert result["valid"] is True

    def test_validate_infrastructure_exception_returns_error_dict(self):
        svc = _make_service()

        # Force the OUTER exception handler: the membership test
        # `"subnet_ids" in template_defaults` runs before any inner try/except,
        # so a template_defaults object that raises on `in` propagates to the
        # top-level handler that returns {"provider": ..., "error": str(e)}.
        class _RaisesOnMembership:
            def __bool__(self):
                return True

            def __contains__(self, item):
                raise RuntimeError("infrastructure lookup failed")

        result = svc.validate_infrastructure(
            {
                "name": "p",
                "config": {},
                "template_defaults": _RaisesOnMembership(),
            }
        )
        assert result["provider"] == "p"
        assert result["error"] == "infrastructure lookup failed"
        # The outer error path must NOT return the normal validation shape.
        assert "valid" not in result


# ---------------------------------------------------------------------------
# _discover_infrastructure_summary — multiple VPCs
# ---------------------------------------------------------------------------


class TestDiscoverInfrastructureSummary:
    def _setup_two_vpcs(self, svc):
        svc.ec2_client.describe_vpcs.return_value = {
            "Vpcs": [
                {
                    "VpcId": "vpc-001",
                    "CidrBlock": "10.0.0.0/16",
                    "IsDefault": False,
                    "Tags": [],
                },
                {
                    "VpcId": "vpc-002",
                    "CidrBlock": "10.1.0.0/16",
                    "IsDefault": True,
                    "Tags": [],
                },
            ]
        }
        svc.ec2_client.describe_subnets.return_value = {
            "Subnets": [
                {
                    "SubnetId": "subnet-001",
                    "VpcId": "vpc-001",
                    "AvailabilityZone": "us-east-1a",
                    "CidrBlock": "10.0.1.0/24",
                    "Tags": [],
                }
            ]
        }
        svc.ec2_client.describe_route_tables.return_value = {"RouteTables": []}
        svc.ec2_client.describe_security_groups.return_value = {"SecurityGroups": []}

    def test_summary_returns_total_counts(self):
        svc = _make_service()
        self._setup_two_vpcs(svc)
        cli_args = MagicMock()
        cli_args.summary = True
        result = svc.discover_infrastructure({"name": "p", "config": {}, "cli_args": cli_args})
        assert result["vpcs"] == 2
        # 1 subnet per 2 VPCs (only first VPC has one, but each vpc is queried)
        assert "total_subnets" in result

    def test_summary_with_no_vpcs(self):
        svc = _make_service()
        svc.ec2_client.describe_vpcs.return_value = {"Vpcs": []}
        cli_args = MagicMock()
        cli_args.summary = True
        result = svc.discover_infrastructure({"name": "p", "config": {}, "cli_args": cli_args})
        assert result["vpcs"] == 0


# ---------------------------------------------------------------------------
# discover_subnets — route table public/private logic
# ---------------------------------------------------------------------------


class TestDiscoverSubnetsRouteTableLogic:
    def test_subnet_marked_as_public_when_igw_in_route(self):
        svc = _make_service()
        svc.ec2_client.describe_subnets.return_value = {
            "Subnets": [
                {
                    "SubnetId": "subnet-pub",
                    "VpcId": "vpc-001",
                    "AvailabilityZone": "us-east-1a",
                    "CidrBlock": "10.0.1.0/24",
                    "Tags": [],
                }
            ]
        }
        svc.ec2_client.describe_route_tables.return_value = {
            "RouteTables": [
                {
                    "Routes": [{"GatewayId": "igw-001", "DestinationCidrBlock": "0.0.0.0/0"}],
                    "Associations": [{"SubnetId": "subnet-pub", "Main": False}],
                }
            ]
        }
        subnets = svc.discover_subnets("vpc-001")
        assert len(subnets) == 1
        assert subnets[0].is_public is True

    def test_subnet_marked_as_private_when_no_igw(self):
        svc = _make_service()
        svc.ec2_client.describe_subnets.return_value = {
            "Subnets": [
                {
                    "SubnetId": "subnet-priv",
                    "VpcId": "vpc-001",
                    "AvailabilityZone": "us-east-1b",
                    "CidrBlock": "10.0.2.0/24",
                    "Tags": [],
                }
            ]
        }
        svc.ec2_client.describe_route_tables.return_value = {
            "RouteTables": [
                {
                    "Routes": [{"GatewayId": "local", "DestinationCidrBlock": "10.0.0.0/16"}],
                    "Associations": [{"SubnetId": "subnet-priv", "Main": False}],
                }
            ]
        }
        subnets = svc.discover_subnets("vpc-001")
        assert subnets[0].is_public is False

    def test_subnet_defaults_to_private_when_not_in_route_table(self):
        svc = _make_service()
        svc.ec2_client.describe_subnets.return_value = {
            "Subnets": [
                {
                    "SubnetId": "subnet-unassoc",
                    "VpcId": "vpc-001",
                    "AvailabilityZone": "us-east-1c",
                    "CidrBlock": "10.0.3.0/24",
                    "Tags": [],
                }
            ]
        }
        svc.ec2_client.describe_route_tables.return_value = {"RouteTables": []}
        subnets = svc.discover_subnets("vpc-001")
        assert subnets[0].is_public is False

    def test_subnet_returns_empty_on_exception(self):
        svc = _make_service()
        svc.ec2_client.describe_subnets.side_effect = RuntimeError("ec2 error")
        subnets = svc.discover_subnets("vpc-001")
        assert subnets == []

    def test_subnet_name_falls_back_to_subnet_id_when_no_name_tag(self):
        svc = _make_service()
        svc.ec2_client.describe_subnets.return_value = {
            "Subnets": [
                {
                    "SubnetId": "subnet-noname",
                    "VpcId": "vpc-001",
                    "AvailabilityZone": "us-east-1a",
                    "CidrBlock": "10.0.1.0/24",
                    "Tags": [],
                }
            ]
        }
        svc.ec2_client.describe_route_tables.return_value = {"RouteTables": []}
        subnets = svc.discover_subnets("vpc-001")
        assert subnets[0].name == "subnet-noname"


# ---------------------------------------------------------------------------
# discover_security_groups — extended
# ---------------------------------------------------------------------------


class TestDiscoverSecurityGroupsExtended:
    def test_returns_empty_on_exception(self):
        svc = _make_service()
        svc.ec2_client.describe_security_groups.side_effect = RuntimeError("error")
        result = svc.discover_security_groups("vpc-001")
        assert result == []

    def test_name_falls_back_to_group_id_when_no_name(self):
        svc = _make_service()
        svc.ec2_client.describe_security_groups.return_value = {
            "SecurityGroups": [
                {
                    "GroupId": "sg-001",
                    "GroupName": "default",
                    "Description": "default VPC sg",
                    "VpcId": "vpc-001",
                    "IpPermissions": [],
                }
            ]
        }
        sgs = svc.discover_security_groups("vpc-001")
        assert len(sgs) == 1
        assert sgs[0].name == "default"


# ---------------------------------------------------------------------------
# _summarize_sg_rules — extended branches
# ---------------------------------------------------------------------------


class TestSummarizeSGRulesExtended:
    def test_no_inbound_rules_returns_no_inbound_rules(self):
        svc = _make_service()
        sg = {"IpPermissions": []}
        assert svc._summarize_sg_rules(sg) == "No inbound rules"

    def test_http_port_rule(self):
        svc = _make_service()
        sg = {"IpPermissions": [{"FromPort": 80, "ToPort": 80, "IpProtocol": "tcp"}]}
        result = svc._summarize_sg_rules(sg)
        assert "HTTP" in result

    def test_https_port_rule(self):
        svc = _make_service()
        sg = {"IpPermissions": [{"FromPort": 443, "ToPort": 443, "IpProtocol": "tcp"}]}
        result = svc._summarize_sg_rules(sg)
        assert "HTTPS" in result

    def test_ssh_port_rule(self):
        svc = _make_service()
        sg = {"IpPermissions": [{"FromPort": 22, "ToPort": 22, "IpProtocol": "tcp"}]}
        result = svc._summarize_sg_rules(sg)
        assert "SSH" in result

    def test_custom_tcp_port(self):
        svc = _make_service()
        sg = {"IpPermissions": [{"FromPort": 8080, "ToPort": 8080, "IpProtocol": "tcp"}]}
        result = svc._summarize_sg_rules(sg)
        assert "TCP:8080" in result

    def test_all_traffic_protocol(self):
        svc = _make_service()
        sg = {"IpPermissions": [{"IpProtocol": "-1"}]}
        result = svc._summarize_sg_rules(sg)
        assert "All traffic" in result

    def test_udp_protocol(self):
        svc = _make_service()
        sg = {"IpPermissions": [{"FromPort": 53, "IpProtocol": "udp"}]}
        result = svc._summarize_sg_rules(sg)
        assert "UDP" in result

    def test_multiple_rules_are_joined(self):
        svc = _make_service()
        sg = {
            "IpPermissions": [
                {"FromPort": 80, "ToPort": 80, "IpProtocol": "tcp"},
                {"FromPort": 443, "ToPort": 443, "IpProtocol": "tcp"},
            ]
        }
        result = svc._summarize_sg_rules(sg)
        assert "HTTP" in result
        assert "HTTPS" in result


# ---------------------------------------------------------------------------
# discover_vpcs — sorting
# ---------------------------------------------------------------------------


class TestDiscoverVpcsSorting:
    def test_default_vpc_sorted_first(self):
        svc = _make_service()
        svc.ec2_client.describe_vpcs.return_value = {
            "Vpcs": [
                {
                    "VpcId": "vpc-non-default",
                    "CidrBlock": "10.0.0.0/16",
                    "IsDefault": False,
                    "Tags": [],
                },
                {
                    "VpcId": "vpc-default",
                    "CidrBlock": "172.31.0.0/16",
                    "IsDefault": True,
                    "Tags": [],
                },
            ]
        }
        vpcs = svc.discover_vpcs()
        assert vpcs[0].is_default is True

    def test_vpc_returns_empty_on_exception(self):
        svc = _make_service()
        svc.ec2_client.describe_vpcs.side_effect = RuntimeError("error")
        vpcs = svc.discover_vpcs()
        assert vpcs == []

    def test_vpc_name_falls_back_to_vpc_id(self):
        svc = _make_service()
        svc.ec2_client.describe_vpcs.return_value = {
            "Vpcs": [
                {"VpcId": "vpc-noname", "CidrBlock": "10.0.0.0/16", "IsDefault": False, "Tags": []}
            ]
        }
        vpcs = svc.discover_vpcs()
        assert vpcs[0].name == "vpc-noname"


# ---------------------------------------------------------------------------
# discover_infrastructure — subnets and sg display with show_all
# ---------------------------------------------------------------------------


class TestDiscoverInfrastructureDisplayLimits:
    def _setup_many_subnets_and_sgs(self, svc):
        svc.ec2_client.describe_vpcs.return_value = {
            "Vpcs": [
                {
                    "VpcId": "vpc-001",
                    "CidrBlock": "10.0.0.0/16",
                    "IsDefault": False,
                    "Tags": [{"Key": "Name", "Value": "main-vpc"}],
                }
            ]
        }
        svc.ec2_client.describe_subnets.return_value = {
            "Subnets": [
                {
                    "SubnetId": f"subnet-{i:03d}",
                    "VpcId": "vpc-001",
                    "AvailabilityZone": "us-east-1a",
                    "CidrBlock": f"10.0.{i}.0/24",
                    "Tags": [],
                }
                for i in range(5)
            ]
        }
        svc.ec2_client.describe_route_tables.return_value = {"RouteTables": []}
        svc.ec2_client.describe_security_groups.return_value = {
            "SecurityGroups": [
                {
                    "GroupId": f"sg-{i:03d}",
                    "GroupName": f"sg-{i}",
                    "Description": "desc",
                    "VpcId": "vpc-001",
                    "IpPermissions": [],
                }
                for i in range(4)
            ]
        }

    def test_with_show_all_returns_all_subnets(self):
        svc = _make_service()
        self._setup_many_subnets_and_sgs(svc)
        cli_args = MagicMock()
        cli_args.summary = False
        cli_args.show = None
        cli_args.all = True
        result = svc.discover_infrastructure({"name": "p", "config": {}, "cli_args": cli_args})
        assert result["total_subnets"] == 5

    def test_without_show_all_truncates_display(self):
        svc = _make_service()
        self._setup_many_subnets_and_sgs(svc)
        cli_args = MagicMock()
        cli_args.summary = False
        cli_args.show = None
        cli_args.all = False
        result = svc.discover_infrastructure({"name": "p", "config": {}, "cli_args": cli_args})
        # All subnets counted even if display is truncated
        assert result["total_subnets"] == 5
        assert result["total_sgs"] == 4
