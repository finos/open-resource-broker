"""
AWS Handler Factory

This module provides a factory for creating AWS handlers based on template types.
It follows the Factory Method pattern to create the appropriate handler for each template.
"""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from orb.providers.aws.infrastructure.services.aws_native_spec_service import (
        NativeSpecServiceProtocol,
    )

from orb.domain.base.ports import ConfigurationPort, LoggingPort
from orb.domain.template.template_aggregate import Template
from orb.providers.aws.domain.template.value_objects import ProviderApi
from orb.providers.aws.exceptions.aws_exceptions import AWSValidationError
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler


class AWSHandlerFactory:
    """
    Factory for creating AWS handlers based on template type.

    This factory creates and caches handlers for different AWS resource types,
    ensuring that only one handler instance exists for each type.
    """

    def __init__(
        self,
        aws_client: AWSClient,
        logger: LoggingPort,
        config: Optional[ConfigurationPort] = None,
        native_spec_service: Optional["NativeSpecServiceProtocol"] = None,
    ) -> None:
        """
        Initialize the factory.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            config: Configuration port for accessing configuration (optional)
            native_spec_service: Pre-resolved NativeSpecService instance (optional).
                When provided, used directly instead of resolving from the DI container.
        """
        self._aws_client = aws_client
        self._logger = logger
        self._config = config
        self._native_spec_service = native_spec_service
        self._handlers: dict[str, AWSHandler] = {}
        self._handler_classes: dict[str, type[AWSHandler]] = {}

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
            raise AWSValidationError(f"Invalid AWS handler type: {handler_type}")

        # Check if we have a registered handler class for this type
        if handler_type not in self._handler_classes:
            self._logger.error("No handler class registered for type: %s", handler_type)
            raise AWSValidationError(f"No handler class registered for type: {handler_type}")

        # Create the handler directly with factory's AWS client
        handler_class = self._handler_classes[handler_type]

        from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
        from orb.providers.aws.infrastructure.launch_template.manager import (
            AWSLaunchTemplateManager,
        )
        from orb.providers.aws.infrastructure.services.aws_native_spec_service import (
            AWSNativeSpecService,
        )
        from orb.providers.aws.utilities.aws_operations import AWSOperations

        config_port = self._config

        # Construct AWSNativeSpecService if application services are available
        aws_native_spec_service = None
        if config_port is not None:
            try:
                from orb.providers.aws.infrastructure.services.aws_native_spec_service import (
                    AWSNativeSpecService,
                )

                if self._native_spec_service is not None:
                    # Use pre-injected service — no container lookup needed
                    aws_native_spec_service = AWSNativeSpecService(
                        native_spec_service=self._native_spec_service,
                        config_port=config_port,
                    )
                else:
                    # Attempt lazy resolution via container as last resort
                    try:
                        from orb.application.services.native_spec_service import NativeSpecService
                        from orb.infrastructure.di.container import get_container

                        container = get_container()
                        aws_native_spec_service = AWSNativeSpecService(
                            native_spec_service=container.get(NativeSpecService),
                            config_port=config_port,
                        )
                    except Exception as e:
                        self._logger.warning(
                            "AWSNativeSpecService unavailable, native spec enrichment disabled: %s",
                            e,
                        )
            except Exception as e:
                self._logger.warning(
                    "AWSNativeSpecService unavailable, native spec enrichment disabled: %s", e
                )

        machine_adapter = AWSMachineAdapter(
            aws_client=self._aws_client,
            logger=self._logger,
        )
        launch_template_manager = AWSLaunchTemplateManager(
            aws_client=self._aws_client,
            logger=self._logger,
            config_port=config_port,
            aws_native_spec_service=aws_native_spec_service,
        )
        aws_ops = AWSOperations(
            aws_client=self._aws_client,
            logger=self._logger,
            config_port=config_port,
        )

        handler = handler_class(
            aws_client=self._aws_client,
            logger=self._logger,
            aws_ops=aws_ops,
            launch_template_manager=launch_template_manager,
            machine_adapter=machine_adapter,
            config_port=config_port,
        )

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
        return self.create_handler(handler_type or "")  # type: ignore[arg-type]

    def _register_handler_classes(self) -> None:
        """Register handler classes for different AWS resource types."""
        # Import handler classes here to avoid circular imports
        from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
        from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import (
            EC2FleetHandler,
        )
        from orb.providers.aws.infrastructure.handlers.run_instances.handler import (
            RunInstancesHandler,
        )
        from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import (
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

    def generate_example_templates(self) -> list[Template]:
        """
        Generate example templates from all registered handlers.

        Returns:
            List of example Template objects from all handlers
        """
        examples = []

        for handler_type, handler_class in self._handler_classes.items():
            if hasattr(handler_class, "get_example_templates"):
                try:
                    handler_examples = handler_class.get_example_templates()
                    examples.extend(handler_examples)
                    self._logger.debug(
                        "Added %d example templates from %s handler",
                        len(handler_examples),
                        handler_type,
                    )
                except Exception as e:
                    self._logger.warning(
                        "Failed to get example templates from %s handler: %s", handler_type, e
                    )

        self._logger.info("Generated %d total example templates", len(examples))
        return examples
