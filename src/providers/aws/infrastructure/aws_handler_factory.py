"""
AWS Handler Factory

This module provides a factory for creating AWS handlers based on template types.
It follows the Factory Method pattern to create the appropriate handler for each template.
"""

from typing import Dict, Type

from domain.base.dependency_injection import injectable
from domain.base.ports import ConfigurationPort, LoggingPort
from domain.template.aggregate import Template
from providers.aws.domain.template.value_objects import ProviderApi
from providers.aws.exceptions.aws_exceptions import AWSValidationError
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.infrastructure.handlers.base_handler import AWSHandler


@injectable
class AWSHandlerFactory:
    """
    Factory for creating AWS handlers based on template type.

    This factory creates and caches handlers for different AWS resource types,
    ensuring that only one handler instance exists for each type.
    """

    def __init__(
        self, aws_client: AWSClient, logger: LoggingPort, config: ConfigurationPort
    ) -> None:
        """
        Initialize the factory.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            config: Configuration port for accessing configuration
        """
        self._aws_client = aws_client
        self._logger = logger
        self._config = config
        self._handlers: Dict[str, AWSHandler] = {}
        self._handler_classes: Dict[str, Type[AWSHandler]] = {}

        # Register handler classes
        self._register_handler_classes()

    @property
    def aws_client(self) -> AWSClient:
        """Get the AWS client instance."""
        return self._aws_client

    def create_handler(self, handler_type: str) -> AWSHandler:
        """
        Create a handler for the specified type.

        Args:
            handler_type: Type of handler to create

        Returns:
            AWSHandler: The created handler

        Raises:
            ValidationError: If the handler type is invalid
        """
        self._logger.debug("Creating handler for type: %s", handler_type)

        # Check if we already have a cached handler for this type
        if handler_type in self._handlers:
            self._logger.debug("Returning cached handler for type: %s", handler_type)
            return self._handlers[handler_type]

        # Validate handler type
        try:
            ProviderApi(handler_type)
        except ValueError:
            self._logger.error("Invalid AWS handler type: %s", handler_type)
            raise AWSValidationError(f"Invalid AWS handler type: {handler_type}") from e

        # Check if we have a registered handler class for this type
        if handler_type not in self._handler_classes:
            self._logger.error("No handler class registered for type: %s", handler_type)
            raise AWSValidationError(f"No handler class registered for type: {handler_type}") from e

        # Create the handler
        handler_class = self._handler_classes[handler_type]

        # Use the DI container to create the handler
        from infrastructure.di.container import get_container

        container = get_container()
        handler = container.get(handler_class)

        # Cache the handler for future use
        self._handlers[handler_type] = handler

        self._logger.debug("Created handler for type: %s", handler_type)
        return handler

    def create_handler_for_template(self, template: Template) -> AWSHandler:
        """
        Create a handler for the specified template.

        Args:
            template: Template to create a handler for

        Returns:
            AWSHandler: The created handler

        Raises:
            ValidationError: If the template has an invalid handler type
        """
        self._logger.debug("Creating handler for template: %s", template.template_id)

        # Get the handler type from the template
        handler_type = template.provider_api

        # Create the handler
        return self.create_handler(handler_type)

    def _register_handler_classes(self) -> None:
        """Register handler classes for different AWS resource types."""
        # Import handler classes here to avoid circular imports
        from providers.aws.infrastructure.handlers.asg_handler import ASGHandler
        from providers.aws.infrastructure.handlers.ec2_fleet_handler import (
            EC2FleetHandler,
        )
        from providers.aws.infrastructure.handlers.run_instances_handler import (
            RunInstancesHandler,
        )
        from providers.aws.infrastructure.handlers.spot_fleet_handler import (
            SpotFleetHandler,
        )

        # Register handler classes
        self._handler_classes = {
            ProviderApi.EC2_FLEET.value: EC2FleetHandler,
            ProviderApi.SPOT_FLEET.value: SpotFleetHandler,
            ProviderApi.ASG.value: ASGHandler,
            ProviderApi.RUN_INSTANCES.value: RunInstancesHandler,
        }

        self._logger.debug("Registered handler classes: %s", list(self._handler_classes.keys()))
