"""AWS-specific domain extensions."""

# Import AWS-specific value objects from their respective modules
from .template.value_objects import (
    AWSFleetId,
    AWSImageId,
    AWSLaunchTemplateId,
    AWSSecurityGroupId,
    AWSSubnetId,
)

__all__: list[str] = [
    "AWSFleetId",
    "AWSImageId",
    "AWSLaunchTemplateId",
    "AWSSecurityGroupId",
    "AWSSubnetId",
]
