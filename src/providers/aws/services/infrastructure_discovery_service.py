"""AWS Infrastructure Discovery Service - Handles infrastructure discovery operations."""

from typing import Any, Optional
from dataclasses import dataclass

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
        session = boto3.Session(profile_name=profile, region_name=region)
        self.ec2_client = session.client('ec2')

    def discover_vpcs(self) -> list[VPCInfo]:
        """Discover VPCs with name tags and CIDR blocks."""
        try:
            response = self.ec2_client.describe_vpcs()
            vpcs = []
            
            for vpc in response['Vpcs']:
                name = self._get_name_tag(vpc.get('Tags', []))
                vpcs.append(VPCInfo(
                    id=vpc['VpcId'],
                    name=name or vpc['VpcId'],
                    cidr_block=vpc['CidrBlock'],
                    is_default=vpc.get('IsDefault', False)
                ))
            
            return sorted(vpcs, key=lambda v: (not v.is_default, v.name))
            
        except Exception as e:
            if self._logger:
                self._logger.error("Failed to discover VPCs: %s", e)
            return []

    def discover_subnets(self, vpc_id: str) -> list[SubnetInfo]:
        """Discover subnets with AZ, type (public/private), CIDR."""
        try:
            response = self.ec2_client.describe_subnets(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            # Get route tables to determine if subnet is public
            route_tables = self.ec2_client.describe_route_tables(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )['RouteTables']
            
            # Build mapping of subnet to public/private
            subnet_public_map = {}
            for rt in route_tables:
                has_igw = any(
                    route.get('GatewayId', '').startswith('igw-')
                    for route in rt.get('Routes', [])
                )
                for assoc in rt.get('Associations', []):
                    if 'SubnetId' in assoc:
                        subnet_public_map[assoc['SubnetId']] = has_igw
            
            subnets = []
            for subnet in response['Subnets']:
                name = self._get_name_tag(subnet.get('Tags', []))
                is_public = subnet_public_map.get(subnet['SubnetId'], False)
                
                subnets.append(SubnetInfo(
                    id=subnet['SubnetId'],
                    name=name or subnet['SubnetId'],
                    vpc_id=subnet['VpcId'],
                    availability_zone=subnet['AvailabilityZone'],
                    cidr_block=subnet['CidrBlock'],
                    is_public=is_public
                ))
            
            return sorted(subnets, key=lambda s: (s.availability_zone, not s.is_public))
            
        except Exception as e:
            if self._logger:
                self._logger.error("Failed to discover subnets: %s", e)
            return []

    def discover_security_groups(self, vpc_id: str) -> list[SecurityGroupInfo]:
        """Discover security groups with descriptions and rule summaries."""
        try:
            response = self.ec2_client.describe_security_groups(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            security_groups = []
            for sg in response['SecurityGroups']:
                rule_summary = self._summarize_sg_rules(sg)
                
                security_groups.append(SecurityGroupInfo(
                    id=sg['GroupId'],
                    name=sg['GroupName'],
                    description=sg['Description'],
                    vpc_id=sg['VpcId'],
                    rule_summary=rule_summary
                ))
            
            return sorted(security_groups, key=lambda sg: sg.name)
            
        except Exception as e:
            if self._logger:
                self._logger.error("Failed to discover security groups: %s", e)
            return []

    def _get_name_tag(self, tags: list) -> Optional[str]:
        """Extract Name tag from AWS tags list."""
        for tag in tags:
            if tag.get('Key') == 'Name':
                return tag.get('Value')
        return None

    def _summarize_sg_rules(self, sg: dict) -> str:
        """Summarize security group rules."""
        ingress_rules = sg.get('IpPermissions', [])
        if not ingress_rules:
            return "No inbound rules"
        
        # Simple rule summary
        rule_types = set()
        for rule in ingress_rules:
            from_port = rule.get('FromPort')
            to_port = rule.get('ToPort')
            protocol = rule.get('IpProtocol', 'unknown')
            
            if protocol == 'tcp':
                if from_port == 80:
                    rule_types.add('HTTP')
                elif from_port == 443:
                    rule_types.add('HTTPS')
                elif from_port == 22:
                    rule_types.add('SSH')
                else:
                    rule_types.add(f'TCP:{from_port}')
            elif protocol == '-1':
                rule_types.add('All traffic')
            else:
                rule_types.add(protocol.upper())
        
        return ', '.join(sorted(rule_types)) if rule_types else "Custom rules"