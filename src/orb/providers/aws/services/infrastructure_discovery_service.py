"""AWS Infrastructure Discovery Service - Handles infrastructure discovery operations."""

from dataclasses import dataclass
from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.console_port import ConsolePort
from orb.infrastructure.adapters.null_console_adapter import NullConsoleAdapter
from orb.infrastructure.logging.logger import get_logger


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

    def __init__(
        self,
        region: str,
        profile: Optional[str],
        logger: Optional[LoggingPort] = None,
        console: Optional[ConsolePort] = None,
    ):
        self.region = region
        self.profile = profile
        self._logger = logger or get_logger(__name__)
        self._console = console or NullConsoleAdapter()

        # Create AWS session and clients
        from botocore.config import Config

        from orb.providers.aws.session_factory import AWSSessionFactory

        _config = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 3})
        session = AWSSessionFactory.create_session(profile=profile, region=region)
        self.ec2_client = session.client("ec2", config=_config)
        self.iam_client = session.client("iam", config=_config)
        self.sts_client = session.client("sts", config=_config)

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

    def _discover_spotfleet_role(self) -> Optional[str]:
        """Construct the SpotFleet service-linked role ARN via STS.

        Uses sts:GetCallerIdentity to get the account ID, then constructs
        the deterministic ARN for the AWS-managed service-linked role.
        """
        try:
            account_id = self.sts_client.get_caller_identity()["Account"]
            arn = (
                f"arn:aws:iam::{account_id}:role/aws-service-role"
                f"/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
            )
            # Best-effort IAM role verification — failure is intentionally ignored
            # as the ARN can still be constructed without confirming the role exists
            try:
                self.iam_client.get_role(RoleName="AWSServiceRoleForEC2SpotFleet")
            except Exception:
                pass  # intentional best-effort check
            return arn
        except Exception:
            return None

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
                    self._console.error("--show flag requires resource types")
                    self._console.info(
                        "Available resources: vpcs, subnets, security-groups (or sg), all"
                    )
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
            self._console.info(f"\nProvider: {provider_config.get('name', 'unknown')}")
            self._console.info(f"Region: {config.get('region', 'us-east-1')}")
            self._console.separator(width=50, char="-")

            if not vpcs:
                self._console.info("No VPCs found")
                return {"provider": provider_config.get("name", "unknown"), "vpcs": 0}

            self._console.info(f"Found {len(vpcs)} VPCs:")
            total_subnets = 0
            total_sgs = 0

            for vpc in vpcs:
                self._console.info(f"  {vpc}")

                if not show_filter or "subnets" in show_filter:
                    subnets = self.discover_subnets(vpc.id)
                    total_subnets += len(subnets)
                    if subnets:
                        self._console.info(f"    Subnets ({len(subnets)}):")
                        display_count = len(subnets) if show_all else min(3, len(subnets))
                        for subnet in subnets[:display_count]:
                            self._console.info(f"      {subnet}")
                        if not show_all and len(subnets) > 3:
                            self._console.info(f"      ... and {len(subnets) - 3} more")

                if not show_filter or "security-groups" in show_filter:
                    sgs = self.discover_security_groups(vpc.id)
                    total_sgs += len(sgs)
                    if sgs:
                        self._console.info(f"    Security Groups ({len(sgs)}):")
                        display_count = len(sgs) if show_all else min(2, len(sgs))
                        for sg in sgs[:display_count]:
                            self._console.info(f"      {sg}")
                        if not show_all and len(sgs) > 2:
                            self._console.info(f"      ... and {len(sgs) - 2} more")

            return {
                "provider": provider_config.get("name", "unknown"),
                "vpcs": len(vpcs),
                "total_subnets": total_subnets,
                "total_sgs": total_sgs,
            }

        except Exception as e:
            self._console.error(f"Failed to discover infrastructure: {e}")
            return {"provider": provider_config.get("name", "unknown"), "error": str(e)}

    def _discover_infrastructure_summary(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover infrastructure summary (counts only)."""
        config = provider_config.get("config", {})
        vpcs = self.discover_vpcs()

        self._console.info(f"\nProvider: {provider_config.get('name', 'unknown')}")
        self._console.info(f"Region: {config.get('region', 'us-east-1')}")
        self._console.separator(width=50, char="-")

        if not vpcs:
            self._console.info("No infrastructure found")
            return {"provider": provider_config.get("name", "unknown"), "vpcs": 0}

        total_subnets = sum(len(self.discover_subnets(vpc.id)) for vpc in vpcs)
        total_sgs = sum(len(self.discover_security_groups(vpc.id)) for vpc in vpcs)

        self._console.info("Infrastructure Summary:")
        self._console.info(f"  VPCs: {len(vpcs)}")
        self._console.info(f"  Subnets: {total_subnets}")
        self._console.info(f"  Security Groups: {total_sgs}")

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
            provider_config.get("config", {})  # config extracted but not used

            self._console.info("Discovering infrastructure...")
            discovered = {}

            # Discover VPCs
            vpcs = self.discover_vpcs()
            if not vpcs:
                self._console.info("No VPCs found, skipping infrastructure discovery")
                return {}

            self._console.info("")
            self._console.info("Found VPCs:")
            for i, vpc in enumerate(vpcs, 1):
                self._console.info(f"  ({i}) {vpc}")

            vpc_choice = input("\nSelect VPC (1): ").strip() or "1"
            try:
                selected_vpc = vpcs[int(vpc_choice) - 1]
            except (ValueError, IndexError):
                self._console.error("Invalid VPC selection, skipping infrastructure discovery")
                return {}

            # Discover subnets
            subnets = self.discover_subnets(selected_vpc.id)
            if subnets:
                self._console.info("")
                self._console.info(f"Found subnets in {selected_vpc.id}:")
                for i, subnet in enumerate(subnets, 1):
                    self._console.info(f"  ({i}) {subnet}")
                self._console.info("  (s) Skip subnet selection")

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
                        self._console.error("Invalid subnet selection, skipping subnets")

            # Discover security groups
            sgs = self.discover_security_groups(selected_vpc.id)
            if sgs:
                self._console.info("")
                self._console.info(f"Found security groups in {selected_vpc.id}:")
                for i, sg in enumerate(sgs, 1):
                    self._console.info(f"  ({i}) {sg}")
                self._console.info("  (s) Skip security group selection")

                sg_choice = input("\nSelect security groups (1): ").strip() or "1"
                if sg_choice.lower() != "s":
                    try:
                        sg_indices = [int(x.strip()) - 1 for x in sg_choice.split(",")]
                        selected_sgs = [sgs[i] for i in sg_indices if 0 <= i < len(sgs)]
                        if selected_sgs:
                            discovered["security_group_ids"] = [sg.id for sg in selected_sgs]
                    except (ValueError, IndexError):
                        self._console.error(
                            "Invalid security group selection, skipping security groups"
                        )

            # Discover fleet role interactively
            self._console.info("")
            auto_fleet_role: Optional[str] = self._discover_spotfleet_role()

            if auto_fleet_role:
                self._console.info(f"  Found Spot Fleet service-linked role: {auto_fleet_role}")
                confirm = input("  Use this role? (Y/n): ").strip().lower()
                if confirm in ("", "y", "yes"):
                    discovered["fleet_role"] = auto_fleet_role
                else:
                    override = input(
                        "  Enter Spot Fleet IAM role ARN (or press Enter to skip): "
                    ).strip()
                    if override:
                        discovered["fleet_role"] = override
            else:
                self._console.info(
                    "  Could not determine Spot Fleet service-linked role automatically."
                )
                manual = input(
                    "  Enter Spot Fleet IAM role ARN (optional, press Enter to skip): "
                ).strip()
                if manual:
                    discovered["fleet_role"] = manual

            if discovered:
                self._console.info("")
                self._console.success("Infrastructure discovered and configured!")
            else:
                self._console.info("No infrastructure selected")

            return discovered

        except Exception as e:
            self._console.error(f"Failed to discover infrastructure: {e}")
            self._console.info("Continuing without infrastructure discovery...")
            return {}

    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate AWS infrastructure configuration."""
        try:
            provider_config.get("config", {})  # config extracted but not used
            template_defaults = provider_config.get("template_defaults", {})

            if not template_defaults:
                self._console.info(
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
                    self._console.success(
                        f"Provider {provider_config.get('name', 'unknown')}: All {len(response['Subnets'])} subnets are valid"
                    )
                except Exception as e:
                    validation_results["valid"] = False
                    validation_results["issues"].append(f"Invalid subnets: {e}")
                    self._console.error(
                        f"Provider {provider_config.get('name', 'unknown')}: Subnet validation failed: {e}"
                    )

            # Validate security groups
            if "security_group_ids" in template_defaults:
                try:
                    response = self.ec2_client.describe_security_groups(
                        GroupIds=template_defaults["security_group_ids"]
                    )
                    self._console.success(
                        f"Provider {provider_config.get('name', 'unknown')}: All {len(response['SecurityGroups'])} security groups are valid"
                    )
                except Exception as e:
                    validation_results["valid"] = False
                    validation_results["issues"].append(f"Invalid security groups: {e}")
                    self._console.error(
                        f"Provider {provider_config.get('name', 'unknown')}: Security group validation failed: {e}"
                    )

            # Validate fleet_role IAM role (may be in config or template_defaults)
            provider_instance_config = provider_config.get("config", {})
            if "fleet_role" not in template_defaults and "fleet_role" in provider_instance_config:
                template_defaults = dict(template_defaults)
                template_defaults["fleet_role"] = provider_instance_config["fleet_role"]
            if "fleet_role" in template_defaults:
                try:
                    fleet_role_arn = template_defaults["fleet_role"]
                    role_name = fleet_role_arn.split("/")[-1]
                    self.iam_client.get_role(RoleName=role_name)
                    self._console.success(
                        f"Provider {provider_config.get('name', 'unknown')}: Fleet role '{role_name}' is valid"
                    )
                except Exception as e:
                    validation_results["valid"] = False
                    validation_results["issues"].append(f"Invalid fleet_role: {e}")
                    self._console.error(
                        f"Provider {provider_config.get('name', 'unknown')}: Fleet role validation failed: {e}"
                    )

            return validation_results

        except Exception as e:
            self._console.error(f"Failed to validate infrastructure: {e}")
            return {"provider": provider_config.get("name", "unknown"), "error": str(e)}
