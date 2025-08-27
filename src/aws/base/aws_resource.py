from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Generic, TypeVar
from enum import Enum
import time
import uuid
from src.helpers.utils import map_known_and_additional_fields, serialize_to_dict
from src.models.base_model import BaseModel
from src.models.base_enum_model import BaseEnumModel

# Define a generic type variable for AWSResource subclasses
# Using this so we can include add and remove instance which use
# EC2 Class that has a dependency on AWSResource.
T = TypeVar("T", bound="AWSResource")


class ResourceStatus(BaseEnumModel):
    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    SHUTTING_DOWN = "shutting-down"
    TERMINATED = "terminated"
    UNKNOWN = "unknown"


class AWSResource(BaseModel, Generic[T], ABC):
    """
    Base class for AWS resources.
    """

    def __init__(self, resourceId=None, creationTime=None, status="PENDING", tags=None, message=""):
        self.resourceId = resourceId or f"res-{uuid.uuid4()}"
        self.creationTime = creationTime or int(time.time())
        self.status = status
        self.tags = tags or {}
        self.message = message
        self.instances: List[T] = []  # List of instances (e.g., EC2Instance)

    # @classmethod
    # def from_dict(cls, data: Dict[str, Any]) -> 'AWSResource':
    #     """
    #     Create an AWSResource object from a dictionary.

    #     :param data: Dictionary containing resource configuration.
    #     :return: An AWSResource object.
    #     """
    #     known_data, additional_properties = map_known_and_additional_fields(cls, data)
    #     resource = cls(**known_data)
    #     resource.additional_options.update(additional_properties)
    #     return resource

    # def to_dict(self) -> Dict[str, Any]:
    #     """
    #     Convert the AWSResource object to a dictionary.

    #     :return: A dictionary representation of the resource.
    #     """
    #     return serialize_to_dict(self)

    def add_tag(self, key: str, value: str) -> None:
        """Add a tag to the resource."""
        self.tags[key] = value

    def remove_tag(self, key: str) -> None:
        """Remove a tag from the resource."""
        self.tags.pop(key, None)

    def get_tag_value(self, key: str) -> Optional[str]:
        """Get the value of a specific tag."""
        return self.tags.get(key)

    def update_status(self, new_status: ResourceStatus, message: Optional[str] = None) -> None:
        """Update the status of the resource."""
        self.status = new_status
        if message is not None:
            self.message = message

    def add_instance(self, instance: T) -> None:
        """
        Add an instance to the list of resources.

        :param instance: The instance to add (e.g., EC2Instance).
        """
        if instance not in self.instances:
            self.instances.append(instance)
            print(f"Instance {instance} added.")
        else:
            print(f"Instance {instance} already exists.")

    def remove_instance(self, instance_id: str) -> None:
        """
        Remove an instance from the list of resources by its ID.

        :param instance_id: The ID of the instance to remove.
        """
        before_count = len(self.instances)
        self.instances = [inst for inst in self.instances if getattr(inst, "resourceId", None) != instance_id]
        after_count = len(self.instances)
        
        if before_count == after_count:
            print(f"Instance with ID {instance_id} not found.")
        else:
            print(f"Instance with ID {instance_id} removed.")

    # def get_property(self, property_name: str) -> Any:
    #     """
    #     Get a property value from either class attributes or additionalProperties.

    #     :param property_name: The name of the property to retrieve.
    #     :return: The value of the property or None if not found.
    #     """
    #     return getattr(self, property_name, None) or self.additionalProperties.get(property_name)

    @abstractmethod
    def __str__(self) -> str:
        """Return a string representation of the resource."""
        pass
