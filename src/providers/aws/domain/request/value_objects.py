"""AWS-specific request value objects."""

from domain.request.value_objects import (
    InstanceId,
    Priority,
    RequestId,
    RequestStatus,
    RequestType,
    ResourceId,
    Tags,
)
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
    "AWSFleetId",
    "AWSImageId",
    # AWS-specific extensions
    "AWSInstanceType",
    "AWSLaunchTemplateId",
    "AWSSecurityGroupId",
    "AWSSubnetId",
    "AWSTags",
    "InstanceId",
    "Priority",
    # Base request value objects
    "RequestId",
    "RequestStatus",
    "RequestType",
    "ResourceId",
    "Tags",
]
