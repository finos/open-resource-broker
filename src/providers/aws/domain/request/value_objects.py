"""AWS-specific request value objects."""

from domain.request.value_objects import (
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

__all__: list[str] = [
    "AWSFleetId",
    "AWSImageId",
    "AWSInstanceType",
    "AWSLaunchTemplateId",
    "AWSSecurityGroupId",
    "AWSSubnetId",
    "AWSTags",
    "Priority",
    "RequestId",
    "RequestStatus",
    "RequestType",
    "ResourceId",
    "Tags",
]
