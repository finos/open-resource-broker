from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
import time
from src.models.base_model import BaseModel
from src.models.aws.ec2_instance import EC2Instance, ResourceStatus
from src.models.base_enum_model import BaseEnumModel
from src.models.provider.request import Request


class MachineStatus(BaseEnumModel):
    """
    Enum representing possible statuses of a machine.
    """
    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    SHUTTING_DOWN = "shutting-down"
    TERMINATED = "terminated"
    RETURNED = "returned"
    UNKNOWN = "unknown"


@dataclass
class Machine(BaseModel):
    """
    Represents a machine instance.

    Attributes:
        machineId (str): The unique identifier of the machine.
        name (str): The fully qualified domain name (FQDN) of the machine.
        priceType (str): The pricing model of the machine (e.g., "on-demand", "spot").
        instanceType (str): The type of the instance (e.g., "t2.micro").
        status (MachineStatus): The current status of the machine.
        result (str): The result of the machine provisioning (e.g., "succeed", "fail").
        privateIpAddress (str): The private IP address of the machine.
        publicIpAddress (str): The public IP address of the machine, if available.
        instanceTags (List[Dict[str, str]]): Tags associated with the machine.
        cloudHostId (str): The cloud host ID of the machine.
        launchTime (int): The launch time of the machine in seconds since epoch.
        runningTime (int): The time when the machine started running in seconds since epoch.
        stoppedTime (int): The time when the machine was stopped in seconds since epoch.
        stoppedReason (str): The reason why the machine was stopped.
        terminatedTime (int): The time when the machine was terminated in seconds since epoch.
        terminatedReason (str): The reason why the machine was terminated.
        failedTime (int): The time when the machine failed in seconds since epoch.
        failedReason (str): The reason why the machine failed.
        returnedTime (int): The time when the machine was returned in seconds since epoch.
        returnId (str): The return ID associated with the machine.
        requestId (str): The ID of the request associated with this machine.
        awsHandler (str): The AWS handler type used for this machine.
        message (str): Additional message or information about the machine.
        additionalProperties (Dict[str, Any]): Additional properties not explicitly defined.
    """
    machineId: str
    name: str
    priceType: str
    instanceType: str
    requestId: str
    awsHandler: str
    resourceId: str
    status: MachineStatus = MachineStatus.PENDING
    result: str = ""
    privateIpAddress: str = ""
    publicIpAddress: str = ""
    instanceTags: List[Dict[str, str]] = field(default_factory=list)
    cloudHostId: str = ""
    launchTime: int = field(default_factory=lambda: int(time.time()))
    runningTime: int = 0
    stoppedTime: int = 0
    stoppedReason: str = ""
    terminatedTime: int = 0
    terminatedReason: str = ""
    failedTime: int = 0
    failedReason: str = ""
    returnedTime: int = 0
    returnId: Optional[str] = None
    message: str = ""
    additionalProperties: Dict[str, Any] = field(default_factory=dict)


    def update_status(self, new_status: MachineStatus, state_reason: Optional[Dict[str, Any]] = None) -> None:
        """
        Update the status of the machine and record relevant timestamps and reasons.

        :param new_status: The new status to set for the machine.
        :param state_reason: Optional dictionary containing state transition reason and time.
        """
        self.status = new_status
        if state_reason:
            transition_time = int(state_reason.get("StateTransitionTime", time.time()).timestamp())
            reason = state_reason.get("Message", "")

            if new_status == MachineStatus.STOPPED:
                self.stoppedTime = transition_time
                self.stoppedReason = reason
            elif new_status == MachineStatus.TERMINATED:
                self.terminatedTime = transition_time
                self.terminatedReason = reason
            elif new_status == MachineStatus.UNKNOWN:
                self.failedTime = transition_time
                self.failedReason = reason
            elif new_status == MachineStatus.RUNNING:
                self.runningTime = transition_time
            elif new_status == MachineStatus.SHUTTING_DOWN:
                self.returnedTime = transition_time


    def format_response(self, long: bool = False) -> Dict[str, Any]:
        """
        Format this Machine object into a response dictionary.

        :param long: Whether to include all fields in the response.
        :return: A dictionary representation of the Machine object.
        """
        if long:
            return self.__dict__
        return {
            "machineId": self.machineId,
            "name": self.name,
            "priceType": self.priceType,
            "instanceType": self.instanceType,
            "status": self.status,
            "result": self.result,
            "privateIpAddress": self.privateIpAddress,
            "publicIpAddress": self.publicIpAddress,
            "launchTime": self.launchTime,
            "message": self.message
        }


    def add_tag(self, key: str, value: str) -> None:
        """
        Add a tag to the machine.

        :param key: The key of the tag.
        :param value: The value of the tag.
        """
        self.instanceTags.append({"Key": key, "Value": value})


    def remove_tag(self, key: str) -> None:
        """
        Remove a tag from the machine.

        :param key: The key of the tag to remove.
        """
        self.instanceTags = [tag for tag in self.instanceTags if tag["Key"] != key]


    def get_tag_value(self, key: str) -> Optional[str]:
        """
        Get the value of a specific tag.

        :param key: The key of the tag to retrieve.
        :return: The value of the tag if found, otherwise None.
        """
        for tag in self.instanceTags:
            if tag["Key"] == key:
                return tag["Value"]
        return None


    @classmethod
    def from_ec2_instance(cls, ec2_instance: EC2Instance, request: Request) -> 'Machine':
        fqdn = ec2_instance.additionalProperties.get('PrivateDnsName') or \
            ec2_instance.additionalProperties.get('PublicDnsName') or \
            ec2_instance.instanceId

        # Map EC2 instance state to MachineStatus
        status_mapping = {
            ResourceStatus.PENDING: MachineStatus.PENDING,
            ResourceStatus.RUNNING: MachineStatus.RUNNING,
            ResourceStatus.SHUTTING_DOWN: MachineStatus.SHUTTING_DOWN,
            ResourceStatus.TERMINATED: MachineStatus.TERMINATED,
            ResourceStatus.STOPPING: MachineStatus.STOPPING,
            ResourceStatus.STOPPED: MachineStatus.STOPPED
        }
        status = status_mapping.get(ec2_instance.state, MachineStatus.UNKNOWN)

        # Determine result based on the status
        if status == MachineStatus.RUNNING:
            result = "succeed"
        elif status in [MachineStatus.TERMINATED, MachineStatus.STOPPED]:
            result = "fail"
        else:
            result = "executing"

        launch_time_seconds = int(ec2_instance.launchTime.timestamp())

        return cls(
            machineId=ec2_instance.instanceId,
            name=fqdn,
            priceType=ec2_instance.additionalProperties.get('InstanceLifecycle', 'ondemand'),
            instanceType=ec2_instance.instanceType,
            requestId=request.requestId,
            awsHandler=request.awsHandler,
            resourceId=request.resourceId,
            status=status,
            result=result,
            privateIpAddress=ec2_instance.privateIpAddress,
            publicIpAddress=ec2_instance.publicIpAddress,
            launchTime=launch_time_seconds,
            message="",
            additionalProperties={
                "subnetId": ec2_instance.subnetId,
                "vpcId": ec2_instance.vpcId,
                "imageId": ec2_instance.imageId,
                "availabilityZone": ec2_instance.availabilityZone,
            }
        )

    def __str__(self) -> str:
        return (
            f"Machine(id={self.machineId}, name={self.name}, status={self.status.value}, "
            f"type={self.instanceType}, priceType={self.priceType})"
        )
