"""AWS-specific machine value objects."""

from domain.machine.value_objects import (
    InstanceType,
    MachineHealth,
    MachineId,
    MachineStatus,
    PrivateIpAddress,
    PublicIpAddress,
    Tags,
)
from providers.aws.domain.template.value_objects import (
    AWSImageId,
    AWSInstanceType,
    AWSSecurityGroupId,
    AWSSubnetId,
    AWSTags,
)

# Re-export all base machine value objects with AWS extensions
__all__: list[str] = [
    "AWSImageId",
    # AWS-specific extensions
    "AWSInstanceType",
    "AWSSecurityGroupId",
    "AWSSubnetId",
    "AWSTags",
    "InstanceType",
    "MachineHealth",
    # Base machine value objects
    "MachineId",
    "MachineStatus",
    "PrivateIpAddress",
    "PublicIpAddress",
    "Tags",
]
