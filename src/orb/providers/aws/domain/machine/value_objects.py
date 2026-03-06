"""AWS-specific machine value objects."""

from orb.domain.base.value_objects import InstanceType, Tags
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.machine_status import MachineStatus
from orb.providers.aws.domain.template.value_objects import (
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
    # Base machine value objects
    "MachineId",
    "MachineStatus",
    "Tags",
]
