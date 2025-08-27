from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from ...models.aws.aws_resource import AWSResource, ResourceStatus
from ...models.aws.ec2_instance import EC2Instance


@dataclass
class EC2Fleet(AWSResource):
    """
    Represents an EC2 Fleet in AWS.
    """
    fleetId: str
    fleetState: ResourceStatus
    targetCapacitySpecification: Dict[str, Any]
    fulfilledCapacity: float = 0
    launchTemplateConfigs: List[Dict[str, Any]] = field(default_factory=list)
    instances: List[EC2Instance] = field(default_factory=list)
    createTime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    additionalProperties: Dict[str, Any] = field(default_factory=dict)

    def update_from_describe_fleet(self, fleet_data: Dict[str, Any]) -> None:
        """
        Update fleet properties from describe_fleets API response.
        """
        self.fleetState = ResourceStatus(fleet_data.get('FleetState', self.fleetState.value))
        self.targetCapacitySpecification = fleet_data.get('TargetCapacitySpecification', self.targetCapacitySpecification)
        self.fulfilledCapacity = fleet_data.get('FulfilledCapacity', self.fulfilledCapacity)
        self.launchTemplateConfigs = fleet_data.get('LaunchTemplateConfigs', self.launchTemplateConfigs)
        self.createTime = fleet_data.get('CreateTime', self.createTime)
        if isinstance(self.createTime, str):
            self.createTime = datetime.fromisoformat(self.createTime).replace(tzinfo=timezone.utc)

        # Store all other properties in additionalProperties
        for key, value in fleet_data.items():
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

    # def get_property(self, propertyName: str) -> Any:
    #     """
    #     Get a property value, checking both class attributes and additionalProperties.
    #     """
    #     return getattr(self, propertyName, None) or self.additionalProperties.get(propertyName)

    # def to_dict(self) -> Dict[str, Any]:
    #     """
    #     Convert the fleet to a dictionary for API calls or logging.
    #     """
    #     base_dict = super().to_dict()
    #     fleet_dict = {
    #         "FleetId": self.fleetId,
    #         "FleetState": self.fleetState.value,
    #         "TargetCapacitySpecification": self.targetCapacitySpecification,
    #         "FulfilledCapacity": self.fulfilledCapacity,
    #         "LaunchTemplateConfigs": self.launchTemplateConfigs,
    #         "Instances": [instance.to_dict() for instance in self.instances],
    #         "CreateTime": self.createTime.isoformat(),
    #         **self.additionalProperties
    #     }
    #     return {**base_dict, **fleet_dict}

    @classmethod
    def from_describe_fleet(cls, fleet_data: Dict[str, Any]) -> 'EC2Fleet':
        """
        Create an EC2Fleet object from describe_fleets API response.
        """
        create_time = fleet_data.get('CreateTime', datetime.now(timezone.utc))
        if isinstance(create_time, str):
            create_time = datetime.fromisoformat(create_time).replace(tzinfo=timezone.utc)

        fleet = cls(
            fleetId=fleet_data['FleetId'],
            fleetState=ResourceStatus(fleet_data['FleetState']),
            targetCapacitySpecification=fleet_data['TargetCapacitySpecification'],
            fulfilledCapacity=fleet_data.get('FulfilledCapacity', 0),
            createTime=create_time
        )
        fleet.update_from_describe_fleet(fleet_data)
        return fleet

    @classmethod
    def from_paginated_describe_fleet(cls, paginator: Any) -> List['EC2Fleet']:
        """
        Create EC2Fleet objects from paginated describe_fleets API response.
        """
        fleets = []
        for page in paginator:
            for fleet_data in page.get('Fleets', []):
                fleets.append(cls.from_describe_fleet(fleet_data))
        return fleets

    @classmethod
    def from_request(cls, request: Any) -> 'EC2Fleet':
        """
        Create an EC2Fleet object based on a Request object.
        
        This method maps request fields to an EC2 Fleet configuration.
        
        :param request: The Request object containing details about the desired fleet.
        :return: An EC2Fleet object representing the request.
        """
        return cls(
            fleetId="",
            fleetState=ResourceStatus.PENDING,
            targetCapacitySpecification={
                "TotalTargetCapacity": request.numRequested,
                "DefaultTargetCapacityType": "on-demand",
                "OnDemandTargetCapacity": 0,
                "SpotTargetCapacity": request.numRequested,
            },
            launchTemplateConfigs=[
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": request.launchTemplateId,
                        "Version": str(request.launchTemplateVersion),
                    },
                    "Overrides": [
                        {"InstanceType": itype} for itype in request.instanceTypes or []
                    ],
                }
            ],
            fulfilledCapacity=0,
            createTime=datetime.now(timezone.utc),
            additionalProperties={},
        )

    def __str__(self) -> str:
        return f"EC2Fleet(id={self.fleetId}, state={self.fleetState.value}, capacity={self.fulfilledCapacity})"
