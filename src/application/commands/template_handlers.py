"""Template command handlers for CQRS pattern."""

from application.base.handlers import BaseCommandHandler
from application.decorators import command_handler
from application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    TemplateCommandResponse,
    UpdateTemplateCommand,
    ValidateTemplateCommand,
)
from domain.base import UnitOfWorkFactory
from domain.base.exceptions import BusinessRuleError, EntityNotFoundError
from domain.base.ports import (
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)
from domain.template.aggregate import Template


@command_handler(CreateTemplateCommand)
class CreateTemplateHandler(BaseCommandHandler[CreateTemplateCommand, TemplateCommandResponse]):
    """
    Handler for creating templates.

    Responsibilities:
    - Validate template configuration
    - Create template aggregate
    - Persist template through repository
    - Publish TemplateCreated domain event
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, event_publisher, error_handler)
        self._uow_factory = uow_factory
        self._container = container

    async def validate_command(self, command: CreateTemplateCommand) -> None:
        """Validate create template command."""
        await super().validate_command(command)
        if not command.template_id:
            raise ValueError("template_id is required") from e
        if not command.provider_api:
            raise ValueError("provider_api is required") from e
        if not command.image_id:
            raise ValueError("image_id is required") from e

    async def execute_command(self, command: CreateTemplateCommand) -> TemplateCommandResponse:
        """Create new template with validation and events."""
        self.logger.info("Creating template: %s", command.template_id)

        try:
            # Get template configuration port for validation
            from domain.base.ports.template_configuration_port import (
                TemplateConfigurationPort,
            )

            template_port = self._container.get(TemplateConfigurationPort)

            # Validate template configuration
            validation_errors = template_port.validate_template_config(command.configuration)
            if validation_errors:
                self.logger.warning(
                    "Template validation failed for %s: %s",  command.template_id, validation_errors
                )
                return TemplateCommandResponse(
                    template_id=command.template_id, validation_errors=validation_errors
                )

            # Create template aggregate
            template = Template.create(
                template_id=command.template_id,
                name=command.name or command.template_id,
                description=command.description,
                provider_api=command.provider_api,
                instance_type=command.instance_type,
                image_id=command.image_id,
                subnet_ids=command.subnet_ids,
                security_group_ids=command.security_group_ids,
                tags=command.tags,
                configuration=command.configuration,
            )

            # Persist template through repository
            with self._uow_factory.create_unit_of_work() as uow:
                # Check if template already exists
                existing_template = uow.templates.get_by_id(command.template_id)
                if existing_template:
                    raise BusinessRuleError(f"Template {command.template_id} already exists") from e

                # Add new template
                uow.templates.add(template)
                uow.commit()

                self.logger.info("Template created successfully: %s", command.template_id)

            return TemplateCommandResponse(template_id=command.template_id)

        except BusinessRuleError as e:
            self.logger.error(
                "Business rule violation creating template %s: %s", command.template_id, e
            )
            return TemplateCommandResponse(
                template_id=command.template_id, validation_errors=[str(e)]
            )
        except Exception as e:
            self.logger.error("Failed to create template %s: %s", command.template_id, e)
            raise


@command_handler(UpdateTemplateCommand)
class UpdateTemplateHandler(BaseCommandHandler[UpdateTemplateCommand, TemplateCommandResponse]):
    """
    Handler for updating templates.

    Responsibilities:
    - Validate template exists
    - Validate updated configuration
    - Update template aggregate
    - Persist changes through repository
    - Publish TemplateUpdated domain event
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._uow_factory = uow_factory
        self._container = container

    async def validate_command(self, command: UpdateTemplateCommand) -> None:
        """Validate update template command."""
        await super().validate_command(command)
        if not command.template_id:
            raise ValueError("template_id is required") from e

    async def execute_command(self, command: UpdateTemplateCommand) -> TemplateCommandResponse:
        """Update existing template with validation and events."""
        self.logger.info("Updating template: %s", command.template_id)

        try:
            # Get template configuration port for validation
            from domain.base.ports.template_configuration_port import (
                TemplateConfigurationPort,
            )

            template_port = self._container.get(TemplateConfigurationPort)

            # Validate updated configuration if provided
            validation_errors = []
            if command.configuration:
                validation_errors = template_port.validate_template_config(command.configuration)
                if validation_errors:
                    self.logger.warning(
                        "Template update validation failed for %s: %s",  command.template_id, validation_errors
                    )
                    return TemplateCommandResponse(
                        template_id=command.template_id,
                        validation_errors=validation_errors,
                    )

            # Update template through repository
            with self._uow_factory.create_unit_of_work() as uow:
                # Get existing template
                template = uow.templates.get_by_id(command.template_id)
                if not template:
                    raise EntityNotFoundError("Template", command.template_id) from e

                # Track changes for event
                changes = {}

                # Update template properties
                if command.name is not None:
                    template.update_name(command.name)
                    changes["name"] = command.name

                if command.description is not None:
                    template.update_description(command.description)
                    changes["description"] = command.description

                if command.configuration:
                    template.update_configuration(command.configuration)
                    changes["configuration"] = command.configuration

                # Save changes
                uow.templates.update(template)
                uow.commit()

                self.logger.info("Template updated successfully: %s", command.template_id)

            return TemplateCommandResponse(template_id=command.template_id)

        except EntityNotFoundError:
            self.logger.error("Template not found for update: %s", command.template_id)
            raise
        except Exception as e:
            self.logger.error("Failed to update template %s: %s", command.template_id, e)
            raise


@command_handler(DeleteTemplateCommand)
class DeleteTemplateHandler(BaseCommandHandler[DeleteTemplateCommand, TemplateCommandResponse]):
    """
    Handler for deleting templates.

    Responsibilities:
    - Validate template exists
    - Check if template is in use
    - Delete template through repository
    - Publish TemplateDeleted domain event
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._uow_factory = uow_factory
        self._container = container

    async def validate_command(self, command: DeleteTemplateCommand) -> None:
        """Validate delete template command."""
        await super().validate_command(command)
        if not command.template_id:
            raise ValueError("template_id is required") from e

    async def execute_command(self, command: DeleteTemplateCommand) -> TemplateCommandResponse:
        """Delete template with validation and events."""
        self.logger.info("Deleting template: %s", command.template_id)

        try:
            # Delete template through repository
            with self._uow_factory.create_unit_of_work() as uow:
                # Get existing template
                template = uow.templates.get_by_id(command.template_id)
                if not template:
                    raise EntityNotFoundError("Template", command.template_id) from e

                # Check if template is in use (business rule)
                # This could be expanded to check for active requests using this
                # template
                if template.is_in_use():
                    raise BusinessRuleError(
                        f"Cannot delete template { command.template_id}: template is in use"
                    )

                # Delete template
                uow.templates.remove(template)
                uow.commit()

                self.logger.info("Template deleted successfully: %s", command.template_id)

            return TemplateCommandResponse(template_id=command.template_id)

        except EntityNotFoundError:
            self.logger.error("Template not found for deletion: %s", command.template_id)
            raise
        except BusinessRuleError:
            self.logger.error(
                "Cannot delete template %s: business rule violation", command.template_id
            )
            raise
        except Exception as e:
            self.logger.error("Failed to delete template %s: %s", command.template_id, e)
            raise


@command_handler(ValidateTemplateCommand)
class ValidateTemplateHandler(BaseCommandHandler[ValidateTemplateCommand, TemplateCommandResponse]):
    """
    Handler for validating template configurations.

    Responsibilities:
    - Validate template configuration against schema
    - Validate provider-specific rules
    - Return detailed validation results
    - Publish TemplateValidated domain event
    """

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._container = container

    async def validate_command(self, command: ValidateTemplateCommand) -> None:
        """Validate template validation command."""
        await super().validate_command(command)
        if not command.template_id:
            raise ValueError("template_id is required") from e
        if not command.configuration:
            raise ValueError("configuration is required") from e

    async def execute_command(self, command: ValidateTemplateCommand) -> TemplateCommandResponse:
        """Validate template configuration with detailed results."""
        self.logger.info("Validating template configuration: %s", command.template_id)

        try:
            # Get template configuration port for validation
            from domain.base.ports.template_configuration_port import (
                TemplateConfigurationPort,
            )

            template_port = self._container.get(TemplateConfigurationPort)

            # Validate template configuration
            validation_errors = template_port.validate_template_config(command.configuration)

            # Log validation results
            if validation_errors:
                self.logger.warning(
                    "Template validation failed for %s: %s",  command.template_id, validation_errors
                )
            else:
                self.logger.info("Template validation passed for %s", command.template_id)

            # Publish validation event (could be useful for monitoring/auditing)
            # This would be handled by the domain event system

            return TemplateCommandResponse(
                template_id=command.template_id, validation_errors=validation_errors
            )

        except Exception as e:
            self.logger.error("Template validation failed for %s: %s", command.template_id, e)
            return TemplateCommandResponse(
                template_id=command.template_id,
                validation_errors=[f"Validation error: {str(e)}"],
            )
