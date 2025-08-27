from typing import TypeVar, Generic, List, Dict, Any
from src.helpers.utils import map_known_and_additional_fields, serialize_to_dict

T = TypeVar("T", bound="BaseModel")


class BaseModel(Generic[T]):
    """
    Base class for all models. Provides common methods like get_property, to_dict, and from_dict.
    """

    def __init__(self, **kwargs):
        self.additionalProperties: Dict[str, Any] = kwargs.get("additionalProperties", {})

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BaseModel':
        """
        Create a model object from a dictionary.

        :param data: Dictionary containing configuration values.
        :return: A model object.
        """
        known_data, additional_properties = map_known_and_additional_fields(cls, data)

        # Handle special cases for nested fields (e.g., machines)
        if cls.__name__ == "Request" and "machines" in known_data:
            from src.models.provider.machine import Machine  # Dynamically import Machine
            known_data["machines"] = [Machine.from_dict(m) for m in known_data["machines"]]

        obj = cls(**known_data)
        obj.additionalProperties.update(additional_properties)
        return obj

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the model object to a dictionary.

        :return: A dictionary representation of the object.
        """
        return serialize_to_dict(self)

    def get_property(self, property_name: str) -> Any:
        """
        Get a property value from either class attributes or additional_options.

        :param property_name: The name of the property to retrieve.
        :return: The value of the property or None if not found.
        """
        return getattr(self, property_name, None) or self.additionalProperties.get(property_name)

    @property
    def __dict__(self):
        return self.to_dict()
