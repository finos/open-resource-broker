from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
import time
import uuid
# from .machine import Machine, MachineStatus
from src.models.base_model import BaseModel
from src.models.base_enum_model import BaseEnumModel


class RequestType(BaseEnumModel):
    """Enum representing types of requests."""
    ACQUIRE = "acquire"
    RETURN = "return"


class RequestStatus(BaseEnumModel):
    """Enum representing possible statuses of a request."""
    RUNNING = "running"
    COMPLETE = "complete"
    COMPLETE_WITH_ERRORS = "complete_with_errors"


@dataclass
class Request(BaseModel):
    """
    Represents a request for machine instances.

    Attributes:
        requestType (RequestType): Type of the request (e.g., acquire or return).
        awsHandler (str): AWS handler used for this request.
        numRequested (int): Number of machines requested.
        templateId (str): ID of the template used for this request.
        requestId (str): Unique ID for this request.
        requestedTime (int): Timestamp when the request was created.
        firstStatusCheckTime (int): Timestamp of the first status check.
        lastStatusCheckTime (int): Timestamp of the last status check.
        status (RequestStatus): Current status of the request.
        message (str): Message associated with the request.
        numRunning (int): Number of machines currently running.
        numFailed (int): Number of machines that failed.
        numReturned (int): Number of machines returned.
        resourceId (str): Resource ID associated with this request.
        launchTemplateId (str): Launch template ID used for this request.
        launchTemplateVersion (str): Version of the launch template used.
        error (Optional[str]): Error message, if any.
        additionalProperties (Dict[str, Any]): Additional properties for the request.
    """
    requestType: RequestType
    awsHandler: str
    numRequested: int
    templateId: str
    requestId: str = field(default_factory=lambda: f"req-{uuid.uuid4()}")
    requestedTime: int = field(default_factory=lambda: int(time.time()))
    firstStatusCheckTime: int = 0
    lastStatusCheckTime: int = 0
    status: RequestStatus = RequestStatus.RUNNING
    message: str = ""
    numRunning: int = 0
    numFailed: int = 0
    numReturned: int = 0
    resourceId: str = ""
    launchTemplateId: str = ""
    launchTemplateVersion: str = ""
    error: Optional[str] = None
    additionalProperties: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def generate_request_id(cls, request_type: RequestType) -> str:
        """
        Generate a unique request ID based on the request type.

        :param request_type: Type of the request ("acquire" or "return").
        :return: A unique request ID.
        """
        if request_type == RequestType.ACQUIRE:
            return f"req-{uuid.uuid4()}"
        elif request_type == RequestType.RETURN:
            return f"ret-{uuid.uuid4()}"
        else:
            raise ValueError("Invalid request type. Must be 'acquire' or 'return'.")

    @classmethod
    def get_request_type(cls, request_id: str) -> str:
        """
        Determine the type of a given request based on its ID.

        :param request_id: The ID of the request.
        :return: "acquire" if it's an acquire request, "return" if it's a return request.
        :raises ValueError: If the request ID format is invalid.
        """
        if request_id.startswith("req-"):
            return "acquire"
        elif request_id.startswith("ret-"):
            return "return"
        else:
            raise ValueError(f"Invalid Request ID format '{request_id}'.")

    def format_response(self, machines: List[Machine], long: bool = False) -> Dict[str, Any]:
        """
        Format this Request object into a response dictionary.

        :param machines: Machines associated with this request.
        :param long: Whether to include all fields in the response.
        :return: A dictionary representation of the response.
        """
        if long:
            response = self.__dict__.copy()
            response['machines'] = [machine.to_dict() for machine in machines]
            return response
        else:
            return {
                "requestId": self.requestId,
                "status": self.status,
                "machines": [machine.format_response(long) for machine in machines],
                "message": f"Status retrieved successfully for {self.requestId}."
            }

    def add_machine(self, machine: Machine) -> None:
        """
        Add a machine to this request.

        :param machine: The Machine object to add.
        :raises ValueError: If the provided machine is not an instance of Machine.
        """
        if not isinstance(machine, Machine):
            raise ValueError("Only Machine objects can be added to the request.")

    def update_machine_counts(self, machines: List[Machine]) -> None:
        """
        Update counts of machines in different states.

        :param machines: Machines associated with this request.
        """
        self.numRunning = sum(1 for m in machines if m.status == MachineStatus.RUNNING)
        self.numFailed = sum(1 for m in machines if m.status == MachineStatus.TERMINATED)
        self.numReturned = sum(1 for m in machines if m.status == MachineStatus.RETURNED)

    def update_machine(self, machine_id: str, new_status: MachineStatus, db_handler) -> None:
        """
        Update the status of a machine in this request.

        :param machine_id: The ID of the machine to update.
        :param new_status: The new status to set for the machine.
        :param db_handler: Database handler to perform the update.
        """
        machine = db_handler.get_machine(machine_id)
        if machine and machine.requestId == self.requestId:
            machine.update_status(new_status)
            db_handler.update_machine(machine)
            self.update_machine_counts(db_handler.get_machines_by_request_id(self.requestId))

    def return_machine(self, machine_id: str, returnId: str, db_handler) -> Optional[Machine]:
        """
        Move a machine from this acquire request to a return request.

        :param machine_id: The ID of the machine to be returned.
        :param returnId: The ID of the return operation.
        :param db_handler: Database handler instance.
        :return: The returned Machine object if successful; otherwise None.
        """
        machine = db_handler.get_machine(machine_id)
        
        if machine and machine.requestId == self.requestId:
            machine.returnId = returnId
            machine.update_status(MachineStatus.RETURNED)
            db_handler.update_machine(machine)
            
            self.update_machine_counts(db_handler.get_machines_by_request_id(self.requestId))
            
            return machine
        
        return None

    def update_status(self, new_status: str, message: Optional[str] = None) -> None:
        """
        Update the status of the request.

        :param new_status: The new status to set (e.g., "running", "complete").
        :param message: Optional message to include with the status update.
        """
        self.status = new_status
        if message is not None:
            self.message = message
        self.lastStatusCheckTime = int(time.time())
        if self.firstStatusCheckTime == 0:
            self.firstStatusCheckTime = self.lastStatusCheckTime

    @property
    def is_complete(self) -> bool:
        """
        Check if the request is complete.

        :return: True if all requested machines are accounted for; otherwise False.
        """
        return self.numRunning + self.numFailed + self.numReturned == self.numRequested

    def set_resource_id(self, resource_id: str) -> None:
        """
        Set the resource ID for this request.

        :param resource_id: The resource ID to set.
        """
        self.resourceId = resource_id

    def set_launch_template_info(self, template_id: str, version: str) -> None:
        """
        Set the launch template information for this request.

        :param template_id: The ID of the launch template.
        :param version: The version of the launch template.
        """
        self.launchTemplateId = template_id
        self.launchTemplateVersion = version

    def __str__(self) -> str:
        """
        Return a string representation of the Request.

        :return: A string describing the Request object.
        """
        return (f"Request(id={self.requestId}, type={self.requestType.value}, "
                f"status={self.status.value}, numRequested={self.numRequested}, "
                f"numRunning={self.numRunning}, numFailed={self.numFailed}, "
                f"numReturned={self.numReturned})")
