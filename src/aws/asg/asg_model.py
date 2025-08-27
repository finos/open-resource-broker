from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from ...models.aws.aws_resource import AWSResource, ResourceStatus
from ...models.aws.ec2_instance import EC2Instance
from src.helpers.utils import paginate
import boto3

@dataclass
class AutoScalingGroup(AWSResource):
    autoScalingGroupName: str
    autoScalingGroupARN: str
    launchTemplate: Dict[str, Any]
    minSize: int
    maxSize: int
    desiredCapacity: int
    defaultCooldown: int
    availabilityZones: List[str] = field(default_factory=list)
    loadBalancerNames: List[str] = field(default_factory=list)
    targetGroupARNs: List[str] = field(default_factory=list)
    healthCheckType: str = "EC2"
    healthCheckGracePeriod: int = 0
    instances: List[EC2Instance] = field(default_factory=list)
    createdTime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    suspendedProcesses: List[str] = field(default_factory=list)
    placementGroup: str = ""
    vpcZoneIdentifier: str = ""
    enabledMetrics: List[str] = field(default_factory=list)
    status: str = ""
    tags: List[Dict[str, str]] = field(default_factory=list)
    terminationPolicies: List[str] = field(default_factory=list)
    newInstancesProtectedFromScaleIn: bool = False
    serviceLinkedRoleARN: str = ""
    maxInstanceLifetime: Optional[int] = None
    capacityRebalance: bool = False
    additionalProperties: Dict[str, Any] = field(default_factory=dict)

    def update_from_describe_auto_scaling_group(self, asg_data: Dict[str, Any]) -> None:
        """Update Auto Scaling Group properties from describe_auto_scaling_groups API response."""
        self.minSize = asg_data.get('MinSize', self.minSize)
        self.maxSize = asg_data.get('MaxSize', self.maxSize)
        self.desiredCapacity = asg_data.get('DesiredCapacity', self.desiredCapacity)
        self.defaultCooldown = asg_data.get('DefaultCooldown', self.defaultCooldown)
        self.availabilityZones = asg_data.get('AvailabilityZones', self.availabilityZones)
        self.loadBalancerNames = asg_data.get('LoadBalancerNames', self.loadBalancerNames)
        self.targetGroupARNs = asg_data.get('TargetGroupARNs', self.targetGroupARNs)
        self.healthCheckType = asg_data.get('HealthCheckType', self.healthCheckType)
        self.healthCheckGracePeriod = asg_data.get('HealthCheckGracePeriod', self.healthCheckGracePeriod)
        self.createdTime = asg_data.get('CreatedTime', self.createdTime)
        if isinstance(self.createdTime, str):
            self.createdTime = datetime.fromisoformat(self.createdTime).replace(tzinfo=timezone.utc)
        self.suspendedProcesses = asg_data.get('SuspendedProcesses', self.suspendedProcesses)
        self.placementGroup = asg_data.get('PlacementGroup', self.placementGroup)
        self.vpcZoneIdentifier = asg_data.get('VPCZoneIdentifier', self.vpcZoneIdentifier)
        self.enabledMetrics = asg_data.get('EnabledMetrics', self.enabledMetrics)
        self.status = asg_data.get('Status', self.status)
        self.tags = asg_data.get('Tags', self.tags)
        self.terminationPolicies = asg_data.get('TerminationPolicies', self.terminationPolicies)
        self.newInstancesProtectedFromScaleIn = asg_data.get('NewInstancesProtectedFromScaleIn', self.newInstancesProtectedFromScaleIn)
        self.serviceLinkedRoleARN = asg_data.get('ServiceLinkedRoleARN', self.serviceLinkedRoleARN)
        self.maxInstanceLifetime = asg_data.get('MaxInstanceLifetime', self.maxInstanceLifetime)
        self.capacityRebalance = asg_data.get('CapacityRebalance', self.capacityRebalance)

        # Store all other properties in additionalProperties
        for key, value in asg_data.items():
            if not hasattr(self, key):
                self.additionalProperties[key] = value

    # def add_instance(self, instance: EC2Instance) -> None:
    #     """
    #     Add an EC2 instance to the Spot Fleet.

    #     :param instance: The EC2Instance object to add.
    #     """
    #     self.instances.append(instance)

    # def remove_instance(self, instance_id: str) -> None:
    #     """
    #     Remove an EC2 instance from the Spot Fleet.

    #     :param instance_id: The ID of the instance to remove.
    #     """
    #     self.instances = [inst for inst in self.instances if inst.instanceId != instance_id]

    # def get_property(self, property_name: str) -> Any:
    #     """Get a property value, checking both class attributes and additionalProperties."""
    #     return getattr(self, property_name, None) or self.additionalProperties.get(property_name)

    # def to_dict(self) -> Dict[str, Any]:
    #     """Convert the Auto Scaling Group to a dictionary, including additional properties."""
    #     base_dict = super().to_dict()
    #     asg_dict = {
    #         "AutoScalingGroupName": self.autoScalingGroupName,
    #         "AutoScalingGroupARN": self.autoScalingGroupARN,
    #         "LaunchTemplate": self.launchTemplate,
    #         "MinSize": self.minSize,
    #         "MaxSize": self.maxSize,
    #         "DesiredCapacity": self.desiredCapacity,
    #         "DefaultCooldown": self.defaultCooldown,
    #         "AvailabilityZones": self.availabilityZones,
    #         "LoadBalancerNames": self.loadBalancerNames,
    #         "TargetGroupARNs": self.targetGroupARNs,
    #         "HealthCheckType": self.healthCheckType,
    #         "HealthCheckGracePeriod": self.healthCheckGracePeriod,
    #         "Instances": [instance.to_dict() for instance in self.instances],
    #         "CreatedTime": self.createdTime.isoformat(),
    #         "SuspendedProcesses": self.suspendedProcesses,
    #         "PlacementGroup": self.placementGroup,
    #         "VPCZoneIdentifier": self.vpcZoneIdentifier,
    #         "EnabledMetrics": self.enabledMetrics,
    #         "Status": self.status,
    #         "Tags": self.tags,
    #         "TerminationPolicies": self.terminationPolicies,
    #         "NewInstancesProtectedFromScaleIn": self.newInstancesProtectedFromScaleIn,
    #         "ServiceLinkedRoleARN": self.serviceLinkedRoleARN,
    #         "MaxInstanceLifetime": self.maxInstanceLifetime,
    #         "CapacityRebalance": self.capacityRebalance,
    #         **self.additionalProperties
    #     }
    #     return {**base_dict, **asg_dict}

    @classmethod
    def from_describe_auto_scaling_group(cls, asg_data: Dict[str, Any]) -> 'AutoScalingGroup':
        """Create an AutoScalingGroup object from describe_auto_scaling_groups API response."""
        created_time = asg_data.get('CreatedTime', datetime.now(timezone.utc))
        if isinstance(created_time, str):
            created_time = datetime.fromisoformat(created_time).replace(tzinfo=timezone.utc)

        asg = cls(
            autoScalingGroupName=asg_data['AutoScalingGroupName'],
            autoScalingGroupARN=asg_data['AutoScalingGroupARN'],
            launchTemplate=asg_data['LaunchTemplate'],
            minSize=asg_data['MinSize'],
            maxSize=asg_data['MaxSize'],
            desiredCapacity=asg_data['DesiredCapacity'],
            defaultCooldown=asg_data['DefaultCooldown'],
            createdTime=created_time
        )
        asg.update_from_describe_auto_scaling_group(asg_data)
        return asg

    @staticmethod
    def list_auto_scaling_groups(region_name: str) -> List['AutoScalingGroup']:
        """
        List all Auto Scaling Groups using paginated describe_auto_scaling_groups API.

        :param region_name: The AWS region to query.
        :return: A list of AutoScalingGroup objects.
        """
        autoscaling_client = boto3.client('autoscaling', region_name=region_name)
        
        asg_data_list = paginate(
            client_method=autoscaling_client.describe_auto_scaling_groups,
            result_key="AutoScalingGroups"
        )

        return [AutoScalingGroup.from_describe_auto_scaling_group(asg_data) for asg_data in asg_data_list]

    def __str__(self) -> str:
        return f"AutoScalingGroup(name={self.autoScalingGroupName}, capacity={self.desiredCapacity}/{self.minSize}-{self.maxSize})"
