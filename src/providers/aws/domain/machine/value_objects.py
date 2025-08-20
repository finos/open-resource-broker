"""AWS-specific machine value objects."""

from domain.machine.value_objects import *
from providers.aws.domain.template.value_objects import (
    AWSImageId,
    AWSInstanceType,
    AWSSecurityGroupId,
    AWSSubnetId,
    AWSTags,
)

# Re-export all base machine value objects with AWS extensions
__all__: list[str] = [
    # Base machine value objects
    "MachineId",
    "MachineStatus",
    "MachineHealth",
    "InstanceType",
    "PrivateIpAddress",
    "PublicIpAddress",
    "Tags",
    # AWS-specific extensions
    "AWSInstanceType",
    "AWSTags",
    "AWSImageId",
    "AWSSubnetId",
    "AWSSecurityGroupId",
]
