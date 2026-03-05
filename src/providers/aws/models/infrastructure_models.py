"""AWS infrastructure models for discovery and selection."""

from dataclasses import dataclass


@dataclass
class VPCInfo:
    """VPC information for selection."""

    id: str
    name: str
    cidr_block: str
    is_default: bool

    def __str__(self) -> str:
        default_marker = " (default)" if self.is_default else ""
        name_part = f" ({self.name})" if self.name and self.name != self.id else ""
        return f"{self.id}{name_part}{default_marker} - {self.cidr_block}"


@dataclass
class SubnetInfo:
    """Subnet information for selection."""

    id: str
    name: str
    vpc_id: str
    availability_zone: str
    cidr_block: str
    is_public: bool

    def __str__(self) -> str:
        subnet_type = "public" if self.is_public else "private"
        name_part = f" ({self.name})" if self.name and self.name != self.id else ""
        return (
            f"{self.id}{name_part} ({self.availability_zone}) - {self.cidr_block} ({subnet_type})"
        )


@dataclass
class SecurityGroupInfo:
    """Security group information for selection."""

    id: str
    name: str
    description: str
    vpc_id: str
    rule_summary: str

    def __str__(self) -> str:
        return f"{self.id} ({self.name}) - {self.rule_summary}"
