"""AWS-specific request value objects."""

from domain.request.value_objects import *
from providers.aws.domain.template.value_objects import (
    AWSFleetId,
    AWSImageId,
    AWSInstanceType,
    AWSLaunchTemplateId,
    AWSSecurityGroupId,
    AWSSubnetId,
    AWSTags,
)

# Re-export all base request value objects with AWS extensions
__all__: list[str] = [
    # Base request value objects
    "RequestId",
    "RequestStatus",
    "RequestType",
    "Priority",
    "ResourceId",
    "InstanceId",
    "Tags",
    # AWS-specific extensions
    "AWSInstanceType",
    "AWSTags",
    "AWSImageId",
    "AWSSubnetId",
    "AWSSecurityGroupId",
    "AWSFleetId",
    "AWSLaunchTemplateId",
]
