import boto3
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from ...models.aws.aws_resource import AWSResource
from ...models.aws.ec2_instance import EC2Instance
from src.helpers.utils import paginate, map_known_and_additional_fields


@dataclass
class RunInstances(AWSResource):
    """
    Represents a RunInstances result in AWS.
    """
    requestId: str
    reservationId: str = ""
    ownerId: str = ""
    instances: List[EC2Instance] = field(default_factory=list)
    requestedInstanceCount: int = 0
    launchedInstanceCount: int = 0
    creationTime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    additionalProperties: Dict[str, Any] = field(default_factory=dict)

    def update_from_run_instances_response(self, run_instances_data: Dict[str, Any]) -> None:
        """
        Update RunInstances properties from run-instances API response.

        :param run_instances_data: Data from run-instances API response.
        """
        self.reservationId = run_instances_data.get("ReservationId", self.reservationId)
        self.ownerId = run_instances_data.get("OwnerId", self.ownerId)

        for instance_data in run_instances_data.get("Instances", []):
            instance = EC2Instance.from_describe_instance(instance_data)
            self.instances.append(instance)

        self.launchedInstanceCount = len(self.instances)

        # Store all other properties in additionalProperties
        for key, value in run_instances_data.items():
            if not hasattr(self, key):
                self.additionalProperties[key] = value

    def add_instance(self, instance: EC2Instance) -> None:
        """Add an EC2 instance to the RunInstances result."""
        self.instances.append(instance)
        self.launchedInstanceCount = len(self.instances)

    def remove_instance(self, instance_id: str) -> None:
        """Remove an EC2 instance from the RunInstances result."""
        self.instances = [inst for inst in self.instances if inst.instanceId != instance_id]
        self.launchedInstanceCount = len(self.instances)

    @classmethod
    def from_run_instances_response(cls, run_instances_data: Dict[str, Any], request_id: str, requested_count: int) -> "RunInstances":
        """
        Create a RunInstances object from run-instances API response.

        :param run_instances_data: Data from run-instances API response.
        :param request_id: The ID of the request.
        :param requested_count: The number of requested instances.
        :return: A RunInstances object populated with data from the response.
        """
        run_instances = cls(
            requestId=request_id,
            reservationId=run_instances_data.get("ReservationId", ""),
            ownerId=run_instances_data.get("OwnerId", ""),
            requestedInstanceCount=requested_count,
        )
        run_instances.update_from_run_instances_response(run_instances_data)
        return run_instances

    @staticmethod
    def list_paginated_reservations(region_name: str) -> List["RunInstances"]:
        """
        Retrieve paginated reservations using the describe-instances API.

        :param region_name: The AWS region to query.
        :return: A list of RunInstances objects representing reservations.
        """
        ec2_client = boto3.client("ec2", region_name=region_name)

        reservations = paginate(
            client_method=ec2_client.describe_instances,
            result_key="Reservations"
        )

        instances_list = []

        for reservation in reservations:
            instances_list.extend([
                EC2Instance.from_describe_instance(instance)
                for instance in reservation.get("Instances", [])
            ])

        return instances_list

    def __str__(self) -> str:
        """
        Return a string representation of the RunInstances object.

        :return: A string describing the RunInstances configuration.
        """
        return (
            f"RunInstances(requestId={self.requestId}, reservationId={self.reservationId}, "
            f"ownerId={self.ownerId}, requestedInstanceCount={self.requestedInstanceCount}, "
            f"launchedInstanceCount={self.launchedInstanceCount})"
        )
