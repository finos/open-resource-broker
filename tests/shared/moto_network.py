"""Network resources the AWS provider needs before it can launch anything."""

from __future__ import annotations

from typing import Any


def seed_moto_network(ec2: Any, region: str) -> dict[str, Any]:
    """Create a VPC, two subnets and a security group through ``ec2``.

    ``ec2`` is a boto3 EC2 client, in-process (``mock_aws``) or pointed at a
    moto server.
    """
    vpc_id = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnet_a = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=f"{region}a"
    )
    subnet_b = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.2.0/24", AvailabilityZone=f"{region}b"
    )
    sg = ec2.create_security_group(
        GroupName="orb-test-sg", Description="ORB moto integration test SG", VpcId=vpc_id
    )
    return {
        "vpc_id": vpc_id,
        "subnet_ids": [subnet_a["Subnet"]["SubnetId"], subnet_b["Subnet"]["SubnetId"]],
        "sg_id": sg["GroupId"],
    }
