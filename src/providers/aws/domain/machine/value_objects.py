"""AWS-specific machine value objects."""

from domain.base.value_objects import InstanceType, Tags
from domain.machine.machine_identifiers import MachineId
from domain.machine.machine_status import MachineStatus
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
    # Base machine value objects
    "MachineId",
    "MachineStatus",
    "Tags",
]
