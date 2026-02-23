"""AWS Infrastructure Discovery Service - Handles infrastructure discovery operations."""

from dataclasses import dataclass
from typing import Any, Optional

import boto3

from domain.base.ports import LoggingPort


@dataclass
class VPCInfo:
    """VPC information."""

    id: str
    name: str
    cidr_block: str
    is_default: bool

    def __str__(self) -> str:
        default_str = " (default)" if self.is_default else ""
        return f"{self.id} ({self.name}){default_str} - {self.cidr_block}"


@dataclass
class SubnetInfo:
    """Subnet information."""

    id: str
    name: str
    vpc_id: str
    availability_zone: str
    cidr_block: str
    is_public: bool

    def __str__(self) -> str:
        subnet_type = "public" if self.is_public else "private"
        return f"{self.id} ({self.availability_zone}) - {self.cidr_block} ({subnet_type})"


@dataclass
class SecurityGroupInfo:
    """Security group information."""

    id: str
    name: str
    description: str
    vpc_id: str
    rule_summary: str

    def __str__(self) -> str:
        return f"{self.id} ({self.name}) - {self.rule_summary}"


class AWSInfrastructureDiscoveryService:
    """Service for AWS infrastructure discovery."""

    def __init__(self, region: str, profile: str, logger: Optional[LoggingPort] = None):
        self.region = region
        self.profile = profile
        self._logger = logger

        # Create AWS session and clients
        from botocore.config import Config
        _config = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 3})
        session = boto3.Session(profile_name=profile, region_name=region)
        self.ec2_client = session.client("ec2", config=_config)

    def discover_vpcs(self) -> list[VPCInfo]:
        """Discover VPCs with name tags and CIDR blocks."""
        try:
            response = self.ec2_client.describe_vpcs()
            vpcs = []

            for vpc in response["Vpcs"]:
                name = self._get_name_tag(vpc.get("Tags", []))
                vpcs.append(
                    VPCInfo(
                        id=vpc["VpcId"],
                        name=name or vpc["VpcId"],
                        cidr_block=vpc["CidrBlock"],
                        is_default=vpc.get("IsDefault", False),
                    )
                )

            return sorted(vpcs, key=lambda v: (not v.is_default, v.name))

        except Exception as e:
            if self._logger:
                self._logger.error("Failed to discover VPCs: %s", e)
            return []

    def discover_subnets(self, vpc_id: str) -> list[SubnetInfo]:
        """Discover subnets with AZ, type (public/private), CIDR."""
        try:
            response = self.ec2_client.describe_subnets(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )

            # Get route tables to determine if subnet is public
            route_tables = self.ec2_client.describe_route_tables(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )["RouteTables"]

            # Build mapping of subnet to public/private
            subnet_public_map = {}
            for rt in route_tables:
                has_igw = any(
                    route.get("GatewayId", "").startswith("igw-") for route in rt.get("Routes", [])
                )
                for assoc in rt.get("Associations", []):
                    if "SubnetId" in assoc:
                        subnet_public_map[assoc["SubnetId"]] = has_igw

            subnets = []
            for subnet in response["Subnets"]:
                name = self._get_name_tag(subnet.get("Tags", []))
                is_public = subnet_public_map.get(subnet["SubnetId"], False)

                subnets.append(
                    SubnetInfo(
                        id=subnet["SubnetId"],
                        name=name or subnet["SubnetId"],
                        vpc_id=subnet["VpcId"],
                        availability_zone=subnet["AvailabilityZone"],
                        cidr_block=subnet["CidrBlock"],
                        is_public=is_public,
                    )
                )

            return sorted(subnets, key=lambda s: (s.availability_zone, not s.is_public))

        except Exception as e:
            if self._logger:
                self._logger.error("Failed to discover subnets: %s", e)
            return []

    def discover_security_groups(self, vpc_id: str) -> list[SecurityGroupInfo]:
        """Discover security groups with descriptions and rule summaries."""
        try:
            response = self.ec2_client.describe_security_groups(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )

            security_groups = []
            for sg in response["SecurityGroups"]:
                rule_summary = self._summarize_sg_rules(sg)

                security_groups.append(
                    SecurityGroupInfo(
                        id=sg["GroupId"],
                        name=sg["GroupName"],
                        description=sg["Description"],
                        vpc_id=sg["VpcId"],
                        rule_summary=rule_summary,
                    )
                )

            return sorted(security_groups, key=lambda sg: sg.name)

        except Exception as e:
            if self._logger:
                self._logger.error("Failed to discover security groups: %s", e)
            return []

    def _get_name_tag(self, tags: list) -> Optional[str]:
        """Extract Name tag from AWS tags list."""
        for tag in tags:
            if tag.get("Key") == "Name":
                return tag.get("Value")
        return None

    def _summarize_sg_rules(self, sg: dict) -> str:
        """Summarize security group rules."""
        ingress_rules = sg.get("IpPermissions", [])
        if not ingress_rules:
            return "No inbound rules"

        # Simple rule summary
        rule_types = set()
        for rule in ingress_rules:
            from_port = rule.get("FromPort")
            rule.get("ToPort")  # to_port not used but extracted for completeness
            protocol = rule.get("IpProtocol", "unknown")

            if protocol == "tcp":
                if from_port == 80:
                    rule_types.add("HTTP")
                elif from_port == 443:
                    rule_types.add("HTTPS")
                elif from_port == 22:
                    rule_types.add("SSH")
                else:
                    rule_types.add(f"TCP:{from_port}")
            elif protocol == "-1":
                rule_types.add("All traffic")
            else:
                rule_types.add(protocol.upper())

        return ", ".join(sorted(rule_types)) if rule_types else "Custom rules"

    def discover_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover AWS infrastructure for provider."""
        try:
            config = provider_config.get("config", {})
            cli_args = provider_config.get("cli_args")

            # Handle summary flag
            if cli_args and getattr(cli_args, "summary", False):
                return self._discover_infrastructure_summary(provider_config)

            # Handle show flag (filter resources)
            show_filter = None
            show_all = False
            if cli_args and hasattr(cli_args, "show") and cli_args.show is not None:
                if not cli_args.show.strip():
                    from cli.console import print_error, print_info

                    print_error("--show flag requires resource types")
                    print_info("Available resources: vpcs, subnets, security-groups (or sg), all")
                    return {
                        "provider": provider_config.get("name", "unknown"),
                        "error": "Invalid --show argument",
                    }

                show_filter = [s.strip() for s in cli_args.show.split(",")]
                show_filter = [f.replace("sg", "security-groups") for f in show_filter]

                if "all" in show_filter:
                    show_all = True
                    show_filter = None

            # Handle all flag
            if cli_args and getattr(cli_args, "all", False):
                show_all = True

            vpcs = self.discover_vpcs()
            from cli.console import print_info, print_separator

            print_info(f"\nProvider: {provider_config.get('name', 'unknown')}")
            print_info(f"Region: {config.get('region', 'us-east-1')}")
            print_separator(width=50, char="-")

            if not vpcs:
                print_info("No VPCs found")
                return {"provider": provider_config.get("name", "unknown"), "vpcs": 0}

            print_info(f"Found {len(vpcs)} VPCs:")
            total_subnets = 0
            total_sgs = 0

            for vpc in vpcs:
                print_info(f"  {vpc}")

                if not show_filter or "subnets" in show_filter:
                    subnets = self.discover_subnets(vpc.id)
                    total_subnets += len(subnets)
                    if subnets:
                        print_info(f"    Subnets ({len(subnets)}):")
                        display_count = len(subnets) if show_all else min(3, len(subnets))
                        for subnet in subnets[:display_count]:
                            print_info(f"      {subnet}")
                        if not show_all and len(subnets) > 3:
                            print_info(f"      ... and {len(subnets) - 3} more")

                if not show_filter or "security-groups" in show_filter:
                    sgs = self.discover_security_groups(vpc.id)
                    total_sgs += len(sgs)
                    if sgs:
                        print_info(f"    Security Groups ({len(sgs)}):")
                        display_count = len(sgs) if show_all else min(2, len(sgs))
                        for sg in sgs[:display_count]:
                            print_info(f"      {sg}")
                        if not show_all and len(sgs) > 2:
                            print_info(f"      ... and {len(sgs) - 2} more")

            return {
                "provider": provider_config.get("name", "unknown"),
                "vpcs": len(vpcs),
                "total_subnets": total_subnets,
                "total_sgs": total_sgs,
            }

        except Exception as e:
            from cli.console import print_error

            print_error(f"Failed to discover infrastructure: {e}")
            return {"provider": provider_config.get("name", "unknown"), "error": str(e)}

    def _discover_infrastructure_summary(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover infrastructure summary (counts only)."""
        from cli.console import print_info, print_separator

        config = provider_config.get("config", {})
        vpcs = self.discover_vpcs()

        print_info(f"\nProvider: {provider_config.get('name', 'unknown')}")
        print_info(f"Region: {config.get('region', 'us-east-1')}")
        print_separator(width=50, char="-")

        if not vpcs:
            print_info("No infrastructure found")
            return {"provider": provider_config.get("name", "unknown"), "vpcs": 0}

        total_subnets = sum(len(self.discover_subnets(vpc.id)) for vpc in vpcs)
        total_sgs = sum(len(self.discover_security_groups(vpc.id)) for vpc in vpcs)

        print_info("Infrastructure Summary:")
        print_info(f"  VPCs: {len(vpcs)}")
        print_info(f"  Subnets: {total_subnets}")
        print_info(f"  Security Groups: {total_sgs}")

        return {
            "provider": provider_config.get("name", "unknown"),
            "vpcs": len(vpcs),
            "total_subnets": total_subnets,
            "total_sgs": total_sgs,
        }

    def discover_infrastructure_interactive(
        self, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Discover AWS infrastructure interactively."""
        try:
            from cli.console import print_error, print_info, print_success

            provider_config.get("config", {})  # config extracted but not used

            print_info("Discovering infrastructure...")
            discovered = {}

            # Discover VPCs
            vpcs = self.discover_vpcs()
            if not vpcs:
                print_info("No VPCs found, skipping infrastructure discovery")
                return {}

            print_info("")
            print_info("Found VPCs:")
            for i, vpc in enumerate(vpcs, 1):
                print_info(f"  ({i}) {vpc}")

            vpc_choice = input("\nSelect VPC (1): ").strip() or "1"
            try:
                selected_vpc = vpcs[int(vpc_choice) - 1]
            except (ValueError, IndexError):
                print_error("Invalid VPC selection, skipping infrastructure discovery")
                return {}

            # Discover subnets
            subnets = self.discover_subnets(selected_vpc.id)
            if subnets:
                print_info("")
                print_info(f"Found subnets in {selected_vpc.id}:")
                for i, subnet in enumerate(subnets, 1):
                    print_info(f"  ({i}) {subnet}")
                print_info("  (s) Skip subnet selection")

                subnet_choice = input("\nSelect subnets (comma-separated) (1,2): ").strip()
                if subnet_choice.lower() != "s":
                    if not subnet_choice:
                        subnet_choice = "1,2" if len(subnets) >= 2 else "1"

                    try:
                        subnet_indices = [int(x.strip()) - 1 for x in subnet_choice.split(",")]
                        selected_subnets = [
                            subnets[i] for i in subnet_indices if 0 <= i < len(subnets)
                        ]
                        if selected_subnets:
                            discovered["subnet_ids"] = [s.id for s in selected_subnets]
                    except (ValueError, IndexError):
                        print_error("Invalid subnet selection, skipping subnets")

            # Discover security groups
            sgs = self.discover_security_groups(selected_vpc.id)
            if sgs:
                print_info("")
                print_info(f"Found security groups in {selected_vpc.id}:")
                for i, sg in enumerate(sgs, 1):
                    print_info(f"  ({i}) {sg}")
                print_info("  (s) Skip security group selection")

                sg_choice = input("\nSelect security groups (1): ").strip() or "1"
                if sg_choice.lower() != "s":
                    try:
                        sg_indices = [int(x.strip()) - 1 for x in sg_choice.split(",")]
                        selected_sgs = [sgs[i] for i in sg_indices if 0 <= i < len(sgs)]
                        if selected_sgs:
                            discovered["security_group_ids"] = [sg.id for sg in selected_sgs]
                    except (ValueError, IndexError):
                        print_error("Invalid security group selection, skipping security groups")

            if discovered:
                print_info("")
                print_success("Infrastructure discovered and configured!")
            else:
                print_info("No infrastructure selected")

            return discovered

        except Exception as e:
            from cli.console import print_error

            print_error(f"Failed to discover infrastructure: {e}")
            print_info("Continuing without infrastructure discovery...")  # type: ignore[possibly-undefined]
            return {}

    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate AWS infrastructure configuration."""
        try:
            from cli.console import print_error, print_info, print_success

            provider_config.get("config", {})  # config extracted but not used
            template_defaults = provider_config.get("template_defaults", {})

            if not template_defaults:
                print_info(
                    f"Provider {provider_config.get('name', 'unknown')}: No infrastructure defaults configured"
                )
                return {
                    "provider": provider_config.get("name", "unknown"),
                    "status": "no_infrastructure_configured",
                    "message": "No infrastructure defaults to validate.",
                }

            validation_results = {
                "provider": provider_config.get("name", "unknown"),
                "valid": True,
                "issues": [],
            }

            # Validate subnets
            if "subnet_ids" in template_defaults:
                try:
                    response = self.ec2_client.describe_subnets(
                        SubnetIds=template_defaults["subnet_ids"]
                    )
                    print_success(
                        f"Provider {provider_config.get('name', 'unknown')}: All {len(response['Subnets'])} subnets are valid"
                    )
                except Exception as e:
                    validation_results["valid"] = False
                    validation_results["issues"].append(f"Invalid subnets: {e}")
                    print_error(
                        f"Provider {provider_config.get('name', 'unknown')}: Subnet validation failed: {e}"
                    )

            # Validate security groups
            if "security_group_ids" in template_defaults:
                try:
                    response = self.ec2_client.describe_security_groups(
                        GroupIds=template_defaults["security_group_ids"]
                    )
                    print_success(
                        f"Provider {provider_config.get('name', 'unknown')}: All {len(response['SecurityGroups'])} security groups are valid"
                    )
                except Exception as e:
                    validation_results["valid"] = False
                    validation_results["issues"].append(f"Invalid security groups: {e}")
                    print_error(
                        f"Provider {provider_config.get('name', 'unknown')}: Security group validation failed: {e}"
                    )

            return validation_results

        except Exception as e:
            from cli.console import print_error

            print_error(f"Failed to validate infrastructure: {e}")
            return {"provider": provider_config.get("name", "unknown"), "error": str(e)}
