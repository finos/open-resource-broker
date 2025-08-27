from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from src.helpers.utils import map_known_and_additional_fields, serialize_to_dict
from src.models.base_model import BaseModel


@dataclass
class ProviderTemplate(BaseModel):
    """
    Represents a cloud provider template.
    """
    templateId: str
    awsHandler: str = "RunInstances"
    priceType: str = "on-demand"
    maxNumber: int = 0
    attributes: Dict[str, Any] = field(default_factory=dict)
    imageId: str = ""
    subnetId: Optional[str] = None
    subnetIds: Optional[List[str]] = None
    vmType: Optional[str] = None
    vmTypes: Optional[Dict[str, int]] = None
    vmTypesOnDemand: Optional[Dict[str, float]] = None
    vmTypesPriority: Optional[Dict[str, float]] = None
    rootDeviceVolumeSize: Optional[int] = None
    volumeType: Optional[str] = None
    iops: Optional[int] = None
    instanceTags: Optional[Dict[str, str]] = None
    keyName: Optional[str] = None
    securityGroupIds: Optional[List[str]] = None
    fleetRole: Optional[str] = None
    maxSpotPrice: Optional[float] = None
    spotFleetRequestExpiry: Optional[int] = None
    allocationStrategy: Optional[str] = None
    allocationStrategyOnDemand: Optional[str] = None
    percentOnDemand: Optional[int] = None
    poolsCount: Optional[int] = None
    instanceProfile: Optional[str] = None
    userDataScript: Optional[str] = None
    launchTemplateId: Optional[str] = None

    # Arbitrary additional options not explicitly defined in the model.
    additionalProperties: Dict[str, Any] = field(default_factory=dict)

    # @classmethod
    # def from_dict(cls, data: Dict[str, Any]) -> 'ProviderTemplate':
    #     """
    #     Create a ProviderTemplate object from a dictionary.

    #     :param data: Dictionary containing template configuration.
    #     :return: A ProviderTemplate object.
    #     """
    #     known_data, additional_properties = map_known_and_additional_fields(cls, data)
    #     template = cls(**known_data)
    #     template.additional_options.update(additional_properties)
    #     return template

    # def to_dict(self) -> Dict[str, Any]:
    #     """
    #     Convert the ProviderTemplate object to a dictionary.

    #     :return: A dictionary representation of the template.
    #     """
    #     return serialize_to_dict(self)

    def validate(self) -> None:
        """
        Validate the template configuration.

        :raises ValueError: If any required fields are missing or invalid.
        """
        required_fields = ['templateId', 'priceType', 'maxNumber', 'imageId']
        
        for field in required_fields:
            if not getattr(self, field):
                raise ValueError(f"Required field '{field}' is missing or empty.")

        if self.subnetId is None and self.subnetIds is None:
            raise ValueError("At least one of subnetId or subnetIds must be specified.")

        if self.maxNumber <= 0:
            raise ValueError("maxNumber must be greater than 0.")

    def __str__(self) -> str:
        return f"ProviderTemplate(templateId={self.templateId}, vmType={self.vmType}, maxNumber={self.maxNumber})"

    def __repr__(self) -> str:
        return self.__str__()
