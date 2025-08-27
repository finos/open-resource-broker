from abc import ABC, abstractmethod
from typing import Any, Dict, List
from ...models.provider.request import Request
from ...models.provider.machine import Machine
from src.helpers.logger import setup_logging

logger = setup_logging()


class BaseAWSHandler(ABC):
    """
    Abstract base class for AWS service-specific handlers.
    Defines the common interface and shared functionality for all AWS handlers.
    """

    @abstractmethod
    def acquire_hosts(self, request: Request) -> Request:
        """
        Acquire hosts based on the given request.

        :param request: The request object containing details about the hosts to acquire.
        :return: The updated request object after acquiring hosts.
        """
        pass

    @abstractmethod
    def release_hosts(self, request: Request) -> Request:
        """
        Release hosts based on the given request.

        :param request: The request object containing details about the hosts to release.
        :return: The updated request object after releasing hosts.
        """
        pass

    @abstractmethod
    def check_request_status(self, request: Request) -> Request:
        """
        Check the status of a given request.

        :param request: The request object to check the status for.
        :return: The updated request object with the current status.
        """
        pass

    @abstractmethod
    def get_config(self, request: Request) -> Dict[str, Any]:
        """
        Generate the configuration for creating or modifying an instance, fleet or ASG.

        :param request: The request object containing details about the fleet configuration.
        :return: A dictionary representing the fleet configuration.
        """
        pass

    def validate_request(self, request: Request) -> None:
        """
        Validate a given request. This method can be overridden by subclasses if additional validation is needed.

        :param request: The request object to validate.
        :raises ValueError: If the validation fails.
        """
        if not request.awsHandler:
            raise ValueError("AWS handler is not specified in the request.")
        
        if not (request.numRequested > 0):
            raise ValueError("The number of requested instances must be greater than zero.")

    def _get_common_tags(self, request: Request) -> Dict[str, str]:
        """
        Generate common tags that are applied across all resources.

        :param request: The Request object containing details about the resources.
        :return: A dictionary of common tags.
        """
        return {
            "RequestId": str(request.requestId),
            "AWSHandler": self.__class__.__name__,
            "Environment": "Production"
        }
