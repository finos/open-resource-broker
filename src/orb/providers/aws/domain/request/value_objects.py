"""AWS-specific request value objects."""

from orb.domain.base.value_objects import ResourceId, Tags
from orb.domain.request.value_objects import (
    RequestId,
    RequestStatus,
    RequestType,
)
from orb.providers.aws.domain.template.value_objects import (
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
    "RequestId",
    "RequestStatus",
    "RequestType",
    "ResourceId",
    "Tags",
]
