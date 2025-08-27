from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime, timezone
from ...models.aws.aws_resource import AWSResource, ResourceStatus
from ...models.aws.ec2_instance import EC2Instance


@dataclass
class SpotFleet(AWSResource):
    """
    Represents a Spot Fleet in AWS.
    """
    spotFleetRequestId: str
    spotFleetRequestState: str
    spotFleetRequestConfig: Dict[str, Any]
    createTime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    targetCapacity: int = 0
    fulfilledCapacity: float = 0
    instances: List[EC2Instance] = field(default_factory=list)
    activityStatus: str = ""
    additionalProperties: Dict[str, Any] = field(default_factory=dict)

    def update_from_describe_spot_fleet(self, spot_fleet_data: Dict[str, Any]) -> None:
        """
        Update Spot Fleet properties from describe-spot-fleet-requests API response.

        :param spot_fleet_data: Data from describe-spot-fleet-requests API response.
        """
        self.spotFleetRequestState = spot_fleet_data.get("SpotFleetRequestState", self.spotFleetRequestState)
        self.spotFleetRequestConfig = spot_fleet_data.get("SpotFleetRequestConfig", self.spotFleetRequestConfig)
        self.createTime = spot_fleet_data.get("CreateTime", self.createTime)
        if isinstance(self.createTime, str):
            self.createTime = datetime.fromisoformat(self.createTime).replace(tzinfo=timezone.utc)
        self.targetCapacity = self.spotFleetRequestConfig.get("TargetCapacity", self.targetCapacity)
        self.fulfilledCapacity = spot_fleet_data.get("FulfilledCapacity", self.fulfilledCapacity)
        self.activityStatus = spot_fleet_data.get("ActivityStatus", self.activityStatus)

        # Store all other properties in additionalProperties
        for key, value in spot_fleet_data.items():
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
    #     """
    #     Get a property value, checking both class attributes and additionalProperties.

    #     :param property_name: The name of the property to retrieve.
    #     :return: The value of the property or None if not found.
    #     """
    #     return getattr(self, property_name, None) or self.additionalProperties.get(property_name)

    # def to_dict(self) -> Dict[str, Any]:
    #     """
    #     Convert the Spot Fleet to a dictionary representation.

    #     Includes both explicitly defined attributes and additional properties.

    #     :return: A dictionary representation of the Spot Fleet.
    #     """
    #     base_dict = super().to_dict()
    #     spot_fleet_dict = {
    #         "SpotFleetRequestId": self.spotFleetRequestId,
    #         "SpotFleetRequestState": self.spotFleetRequestState,
    #         "SpotFleetRequestConfig": self.spotFleetRequestConfig,
    #         "CreateTime": self.createTime.isoformat(),
    #         "TargetCapacity": self.targetCapacity,
    #         "FulfilledCapacity": self.fulfilledCapacity,
    #         "Instances": [instance.to_dict() for instance in self.instances],
    #         "ActivityStatus": self.activityStatus,
    #         **self.additionalProperties,
    #     }
    #     return {**base_dict, **spot_fleet_dict}

    @classmethod
    def from_describe_spot_fleet(cls, spot_fleet_data: Dict[str, Any]) -> "SpotFleet":
        """
        Create a SpotFleet object from describe-spot-fleet-requests API response.

        :param spot_fleet_data: Data from describe-spot-fleet-requests API response.
        :return: A SpotFleet object populated with data from the response.
        """
        create_time = spot_fleet_data.get("CreateTime", datetime.now(timezone.utc))
        if isinstance(create_time, str):
            create_time = datetime.fromisoformat(create_time).replace(tzinfo=timezone.utc)

        spot_fleet = cls(
            spotFleetRequestId=spot_fleet_data["SpotFleetRequestId"],
            spotFleetRequestState=spot_fleet_data["SpotFleetRequestState"],
            spotFleetRequestConfig=spot_fleet_data["SpotFleetRequestConfig"],
            createTime=create_time,
            targetCapacity=spot_fleet_data["SpotFleetRequestConfig"].get("TargetCapacity", 0),
            fulfilledCapacity=spot_fleet_data.get("FulfilledCapacity", 0),
            activityStatus=spot_fleet_data.get("ActivityStatus", ""),
        )
        spot_fleet.update_from_describe_spot_fleet(spot_fleet_data)
        return spot_fleet

    def __str__(self) -> str:
        return f"SpotFleet(id={self.spotFleetRequestId}, state={self.spotFleetRequestState}, capacity={self.fulfilledCapacity}/{self.targetCapacity})"
