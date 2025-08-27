from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import os
from src.helpers.utils import map_known_and_additional_fields
from src.models.base_model import BaseModel


@dataclass
class ProviderConfig(BaseModel):
    """
    Represents the AWS provider configuration, including database and logging settings.
    Dynamically handles both predefined and arbitrary configuration parameters.
    """
    AWS_CREDENTIAL_FILE: Optional[str] = None
    AWS_REGION: Optional[str] = None
    DATABASE_TYPE: str = "json"  # Default to JSON; can be "dynamodb", "sqlite", etc.
    DATABASE_PATH: Optional[str] = None
    DATABASE_FILE_NAME: Optional[str] = None
    DATABASE_TABLE: Optional[str] = None
    LOG_DIR: Optional[str] = None
    LOG_FILE_NAME: Optional[str] = None
    AWS_KEY_FILE: Optional[str] = None
    AWS_PROXY_HOST: Optional[str] = None
    AWS_PROXY_PORT: Optional[int] = None
    AWS_ENDPOINT_URL: Optional[str] = None
    AWS_CONNECTION_TIMEOUT_MS: int = 10000
    AWS_REQUEST_RETRY_ATTEMPTS: int = 0
    AWS_INSTANCE_PENDING_TIMEOUT_SEC: int = 180
    AWS_DESCRIBE_REQUEST_RETRY_ATTEMPTS: int = 0
    AWS_DESCRIBE_REQUEST_INTERVAL: int = 0

    # Store all additional fields dynamically
    additionalProperties: Dict[str, Any] = field(default_factory=dict)

    # @classmethod
    # def from_dict(cls, data: Dict[str, Any]) -> 'ProviderConfig':
    #     """
    #     Create a ProviderConfig object from a dictionary.

    #     :param data: Dictionary containing configuration values.
    #     :return: A ProviderConfig object.
    #     """
    #     known_data, additional_properties = map_known_and_additional_fields(cls, data)
    #     config = cls(**known_data)
    #     config.additional_options.update(additional_properties)
    #     return config

    # def to_dict(self) -> Dict[str, Any]:
    #     """
    #     Convert the ProviderConfig object to a dictionary.

    #     :return: A dictionary representation of the configuration.
    #     """
    #     result = {
    #         field: getattr(self, field)
    #         for field in self.__annotations__
    #         if getattr(self, field) is not None and field != 'additional_options'
    #     }
    #     result.update(self.additional_options)
    #     return result

    def validate(self) -> None:
        """
        Validate the configuration. Raises ValueError if any required fields are missing or invalid.

        :raises ValueError: If validation fails.
        """
        # Required fields validation
        if not self.AWS_CREDENTIAL_FILE:
            raise ValueError("AWS_CREDENTIAL_FILE is required.")
        if not self.AWS_REGION:
            raise ValueError("AWS_REGION is required.")
        
        # Proxy validation
        if self.AWS_PROXY_HOST and not self.AWS_PROXY_PORT:
            raise ValueError("AWS_PROXY_PORT is required when AWS_PROXY_HOST is set.")
        
        # Retry attempts validation
        if not (0 <= self.AWS_REQUEST_RETRY_ATTEMPTS <= 10):
            raise ValueError("AWS_REQUEST_RETRY_ATTEMPTS must be between 0 and 10.")
        
        # Timeout validation
        if not (180 <= self.AWS_INSTANCE_PENDING_TIMEOUT_SEC <= 10000):
            raise ValueError("AWS_INSTANCE_PENDING_TIMEOUT_SEC must be between 180 and 10000.")

        # Database validation
        if not self.DATABASE_TYPE:
            raise ValueError("DATABASE_TYPE is required")
        
        if self.DATABASE_TYPE.lower() in ["json", "sqlite"]:
            if not self.DATABASE_PATH:
                raise ValueError("DATABASE_PATH is required for JSON and SQLite databases")
        
        elif self.DATABASE_TYPE.lower() == "dynamodb":
            if not self.DATABASE_TABLE:
                raise ValueError("DATABASE_TABLE is required for DynamoDB")
        
        else:
            raise ValueError(f"Unsupported database type: {self.DATABASE_TYPE}")

        # Logging directory validation
        if not self.LOG_DIR:
            raise ValueError("LOG_DIR is required")

        # Ensure default values for file names if not provided
        if not self.DATABASE_FILE_NAME:
            self.DATABASE_FILE_NAME = f"{os.getenv('HF_PROVIDER_NAME', 'default')}_database.{self.DATABASE_TYPE.lower()}"
        
        if not self.LOG_FILE_NAME:
            self.LOG_FILE_NAME = f"{os.getenv('HF_PROVIDER_NAME', 'default')}_log.log"
