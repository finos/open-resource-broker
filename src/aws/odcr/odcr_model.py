from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from ...models.aws.aws_resource import AWSResource, ResourceStatus
from src.helpers.utils import paginate
import boto3

@dataclass
class OnDemandCapacityReservation(AWSResource):
    capacityReservationId: str
    ownerId: str
    capacityReservationArn: str
    instanceType: str
    instancePlatform: str
    availabilityZone: str
    tenancy: str
    totalInstanceCount: int
    availableInstanceCount: int
    ebsOptimized: bool
    ephemeralStorage: bool
    state: str
    startDate: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    endDate: Optional[datetime] = None
    endDateType: str = "unlimited"
    instanceMatchCriteria: str = "open"
    createDate: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: Dict[str, str] = field(default_factory=dict)
    outpostArn: Optional[str] = None
    additionalProperties: Dict[str, Any] = field(default_factory=dict)

    def update_from_describe_capacity_reservations(self, reservation_data: Dict[str, Any]) -> None:
        """Update Capacity Reservation properties from describe_capacity_reservations API response."""
        self.ownerId = reservation_data.get('OwnerId', self.ownerId)
        self.capacityReservationArn = reservation_data.get('CapacityReservationArn', self.capacityReservationArn)
        self.instanceType = reservation_data.get('InstanceType', self.instanceType)
        self.instancePlatform = reservation_data.get('InstancePlatform', self.instancePlatform)
        self.availabilityZone = reservation_data.get('AvailabilityZone', self.availabilityZone)
        self.tenancy = reservation_data.get('Tenancy', self.tenancy)
        self.totalInstanceCount = reservation_data.get('TotalInstanceCount', self.totalInstanceCount)
        self.availableInstanceCount = reservation_data.get('AvailableInstanceCount', self.availableInstanceCount)
        self.ebsOptimized = reservation_data.get('EbsOptimized', self.ebsOptimized)
        self.ephemeralStorage = reservation_data.get('EphemeralStorage', self.ephemeralStorage)
        self.state = reservation_data.get('State', self.state)
        
        start_date = reservation_data.get('StartDate')
        if start_date:
            self.startDate = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        
        end_date = reservation_data.get('EndDate')
        if end_date:
            self.endDate = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
        
        self.endDateType = reservation_data.get('EndDateType', self.endDateType)
        self.instanceMatchCriteria = reservation_data.get('InstanceMatchCriteria', self.instanceMatchCriteria)
        
        create_date = reservation_data.get('CreateDate')
        if create_date:
            self.createDate = datetime.fromisoformat(create_date).replace(tzinfo=timezone.utc)
        
        self.tags = {tag['Key']: tag['Value'] for tag in reservation_data.get('Tags', [])}
        self.outpostArn = reservation_data.get('OutpostArn', self.outpostArn)

        # Store all other properties in additionalProperties
        for key, value in reservation_data.items():
            if not hasattr(self, key):
                self.additionalProperties[key] = value

    # def get_property(self, property_name: str) -> Any:
    #     """Get a property value, checking both class attributes and additionalProperties."""
    #     return getattr(self, property_name, None) or self.additionalProperties.get(property_name)

    # def to_dict(self) -> Dict[str, Any]:
    #     """Convert the Capacity Reservation to a dictionary, including additional properties."""
    #     base_dict = super().to_dict()
    #     reservation_dict = {
    #         "CapacityReservationId": self.capacityReservationId,
    #         "OwnerId": self.ownerId,
    #         "CapacityReservationArn": self.capacityReservationArn,
    #         "InstanceType": self.instanceType,
    #         "InstancePlatform": self.instancePlatform,
    #         "AvailabilityZone": self.availabilityZone,
    #         "Tenancy": self.tenancy,
    #         "TotalInstanceCount": self.totalInstanceCount,
    #         "AvailableInstanceCount": self.availableInstanceCount,
    #         "EbsOptimized": self.ebsOptimized,
    #         "EphemeralStorage": self.ephemeralStorage,
    #         "State": self.state,
    #         "StartDate": self.startDate.isoformat(),
    #         "EndDate": self.endDate.isoformat() if self.endDate else None,
    #         "EndDateType": self.endDateType,
    #         "InstanceMatchCriteria": self.instanceMatchCriteria,
    #         "CreateDate": self.createDate.isoformat(),
    #         "Tags": [{"Key": k, "Value": v} for k, v in self.tags.items()],
    #         "OutpostArn": self.outpostArn,
    #         **self.additionalProperties
    #     }
    #     return {**base_dict, **reservation_dict}

    @classmethod
    def from_describe_capacity_reservations(cls, reservation_data: Dict[str, Any]) -> 'OnDemandCapacityReservation':
        """Create an OnDemandCapacityReservation object from describe_capacity_reservations API response."""
        reservation = cls(
            capacityReservationId=reservation_data['CapacityReservationId'],
            ownerId=reservation_data['OwnerId'],
            capacityReservationArn=reservation_data['CapacityReservationArn'],
            instanceType=reservation_data['InstanceType'],
            instancePlatform=reservation_data['InstancePlatform'],
            availabilityZone=reservation_data['AvailabilityZone'],
            tenancy=reservation_data['Tenancy'],
            totalInstanceCount=reservation_data['TotalInstanceCount'],
            availableInstanceCount=reservation_data['AvailableInstanceCount'],
            ebsOptimized=reservation_data['EbsOptimized'],
            ephemeralStorage=reservation_data['EphemeralStorage'],
            state=reservation_data['State']
        )
        reservation.update_from_describe_capacity_reservations(reservation_data)
        return reservation

    @staticmethod
    def list_capacity_reservations(region_name: str) -> List['OnDemandCapacityReservation']:
        """
        List all On-Demand Capacity Reservations using paginated describe_capacity_reservations API.

        :param region_name: The AWS region to query.
        :return: A list of OnDemandCapacityReservation objects.
        """
        ec2_client = boto3.client('ec2', region_name=region_name)
        
        reservation_data_list = paginate(
            client_method=ec2_client.describe_capacity_reservations,
            result_key="CapacityReservations"
        )

        return [OnDemandCapacityReservation.from_describe_capacity_reservations(reservation_data) 
                for reservation_data in reservation_data_list]

    def __str__(self) -> str:
        return f"OnDemandCapacityReservation(id={self.capacityReservationId}, type={self.instanceType}, state={self.state}, available={self.availableInstanceCount}/{self.totalInstanceCount})"
