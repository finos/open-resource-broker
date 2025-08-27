from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from .aws_resource import AWSResource, ResourceStatus
from src.helpers.utils import paginate
import boto3
from botocore.exceptions import ClientError


@dataclass
class EC2Instance(AWSResource):
    """
    Represents an EC2 instance in AWS.

    Attributes:
        instanceId (str): The unique identifier of the instance.
        instanceType (str): The type of the instance.
        privateIpAddress (str): The private IP address of the instance.
        subnetId (str): The subnet ID associated with the instance.
        vpcId (str): The VPC ID associated with the instance.
        imageId (str): The AMI ID used to launch the instance.
        availabilityZone (str): The availability zone where the instance resides.
        publicIpAddress (Optional[str]): The public IP address of the instance.
        state (ResourceStatus): The current state of the instance.
        securityGroups (List[Dict[str, str]]): Security groups associated with the instance.
        tags (Dict[str, str]): Tags associated with the instance.
        additionalProperties (Dict[str, Any]): Additional properties not explicitly defined.
    """
    # Non-default arguments first
    instanceId: str
    instanceType: str
    privateIpAddress: str
    subnetId: str
    vpcId: str
    imageId: str
    availabilityZone: str

    # Default arguments next
    launchTime: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    publicIpAddress: Optional[str] = None
    state: ResourceStatus = ResourceStatus.PENDING
    securityGroups: List[Dict[str, str]] = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)  # Overrides AWSResource's tags field
    additionalProperties: Dict[str, Any] = field(default_factory=dict)

    def update_from_describe_instance(self, instance_data: Dict[str, Any]) -> None:
        """
        Update instance properties from describe-instances API response.

        Dynamically captures all fields and stores unknown fields in additionalProperties.

        :param instance_data: Data from describe-instances API response.
        """
        for key, value in instance_data.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                self.additionalProperties[key] = value

        # Handle nested fields explicitly
        self.state = ResourceStatus(instance_data.get("State", {}).get("Name", self.state.value))
        self.availabilityZone = instance_data.get("Placement", {}).get("AvailabilityZone", self.availabilityZone)
        self.tags = {tag["Key"]: tag["Value"] for tag in instance_data.get("Tags", [])}

        # Convert launchTime to datetime if needed
        launch_time = instance_data.get("LaunchTime")
        if isinstance(launch_time, str):
            self.launchTime = datetime.fromisoformat(launch_time).replace(tzinfo=timezone.utc)
        elif isinstance(launch_time, datetime):
            self.launchTime = launch_time

    @classmethod
    def from_describe_instance(cls, instance_data: Dict[str, Any]) -> "EC2Instance":
        """
        Create an EC2Instance object from describe-instances API response.

        Dynamically captures all fields and handles missing required fields.

        :param instance_data: Data from describe-instances API response.
        :return: An EC2Instance object populated with data from the response.
        """
        try:
            # Extract required fields with defaults for missing values
            instance_id = instance_data.get("InstanceId", "")
            instance_type = instance_data.get("InstanceType", "")
            private_ip_address = instance_data.get("PrivateIpAddress", "")
            subnet_id = instance_data.get("SubnetId", "")
            vpc_id = instance_data.get("VpcId", "")
            image_id = instance_data.get("ImageId", "")
            availability_zone = instance_data.get("Placement", {}).get("AvailabilityZone", "")

            # Create the instance with required fields
            ec2_instance = cls(
                instanceId=instance_id,
                instanceType=instance_type,
                privateIpAddress=private_ip_address,
                subnetId=subnet_id,
                vpcId=vpc_id,
                imageId=image_id,
                availabilityZone=availability_zone
            )

            # Update with all other fields
            ec2_instance.update_from_describe_instance(instance_data)

            return ec2_instance
        except Exception as e:
            raise ValueError(f"Error creating EC2Instance from describe-instances data: {e}")

    @staticmethod
    def get_instance_details(machine_ids: List[str], region_name: str) -> List["EC2Instance"]:
        """
        Retrieve details about specific instances using their IDs.

        :param machine_ids: A list of machine IDs to retrieve details for.
        :param region_name: The AWS region to query for instances.
        :return: A list of EC2Instance objects representing the details of each machine.
        """
        ec2_client = boto3.client("ec2", region_name=region_name)

        try:
            reservations = paginate(
                client_method=ec2_client.describe_instances,
                result_key="Reservations",
                InstanceIds=machine_ids,
            )

            instances = []
            for reservation in reservations:
                for instance_data in reservation.get("Instances", []):
                    instances.append(EC2Instance.from_describe_instance(instance_data))

            return instances

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            raise RuntimeError(f"Failed to retrieve instance details: {error_code}") from e

    def __str__(self) -> str:
        """String representation of an EC2Instance."""
        return (
            f"EC2Instance(id={self.instanceId}, type={self.instanceType}, state={self.state.value}, "
            f"privateIp={self.privateIpAddress}, publicIp={self.publicIpAddress}, "
            f"subnet={self.subnetId}, vpc={self.vpcId}, image={self.imageId}, az={self.availabilityZone})"
        )
