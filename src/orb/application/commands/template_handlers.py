"""Template command handlers for CQRS pattern."""

from orb.application.base.handlers import BaseCommandHandler
from orb.application.decorators import command_handler
from orb.application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    UpdateTemplateCommand,
)
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import BusinessRuleError, EntityNotFoundError
from orb.domain.base.ports import (
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)
from orb.domain.template.template_aggregate import Template


@command_handler(CreateTemplateCommand)  # type: ignore[arg-type]
class CreateTemplateHandler(BaseCommandHandler[CreateTemplateCommand, None]):  # type: ignore[type-var]
    """
    Handler for creating templates.

    Responsibilities:
    - Validate template configuration
    - Create template aggregate
    - Persist template through repository
    - Publish TemplateCreated domain event

    CQRS Compliance: Returns None. Results stored in command.validation_errors and command.created.
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
            raise ValueError("template_id is required")
        if not command.provider_api:
            raise ValueError("provider_api is required")
        if not command.image_id:
            raise ValueError("image_id is required")

    async def execute_command(self, command: CreateTemplateCommand) -> None:
        """Create new template with validation and events."""
        self.logger.info("Creating template: %s", command.template_id)

        try:
            # Get template configuration port for validation
            from orb.domain.base.ports.template_configuration_port import (
                TemplateConfigurationPort,
            )

            template_port = self._container.get(TemplateConfigurationPort)

            # Validate template configuration — merge command fields with extra config
            validation_errors = template_port.validate_template_config(
                {
                    "template_id": command.template_id,
                    "provider_api": command.provider_api,
                    "image_id": command.image_id,
                    **command.configuration,
                }
            )
            if validation_errors:
                self.logger.warning(
                    "Template validation failed for %s: %s",
                    command.template_id,
                    validation_errors,
                )
                command.validation_errors = validation_errors
                command.created = False
                return

            # Create template aggregate
            template = Template(
                template_id=command.template_id,
                name=command.name or command.template_id,
                description=command.description,
                provider_api=command.provider_api,
                instance_type=command.instance_type,
                image_id=command.image_id,
                subnet_ids=command.configuration.get("subnet_ids", []),
                security_group_ids=command.configuration.get("security_group_ids", []),
                tags=command.tags,
            )

            # Persist template through repository
            with self._uow_factory.create_unit_of_work() as uow:
                # Check if template already exists
                from orb.domain.template.value_objects import TemplateId

                existing_template = uow.templates.get_by_id(TemplateId(value=command.template_id))
                if existing_template:
                    raise BusinessRuleError(f"Template {command.template_id} already exists")

                # Add new template
                uow.templates.save(template)

                self.logger.info("Template created successfully: %s", command.template_id)

            command.created = True

        except BusinessRuleError as e:
            self.logger.error(
                "Business rule violation creating template %s: %s",
                command.template_id,
                e,
            )
            command.validation_errors = [str(e)]
            command.created = False
        except Exception as e:
            self.logger.error("Failed to create template %s: %s", command.template_id, e)
            raise


@command_handler(UpdateTemplateCommand)  # type: ignore[arg-type]
class UpdateTemplateHandler(BaseCommandHandler[UpdateTemplateCommand, None]):  # type: ignore[type-var]
    """
    Handler for updating templates.

    Responsibilities:
    - Validate template exists
    - Validate updated configuration
    - Update template aggregate
    - Persist changes through repository
    - Publish TemplateUpdated domain event

    CQRS Compliance: Returns None. Results stored in command.validation_errors and command.updated.
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
            raise ValueError("template_id is required")

    async def execute_command(self, command: UpdateTemplateCommand) -> None:
        """Update existing template with validation and events."""
        self.logger.info("Updating template: %s", command.template_id)

        try:
            # Get template configuration port for validation
            from orb.domain.base.ports.template_configuration_port import (
                TemplateConfigurationPort,
            )

            template_port = self._container.get(TemplateConfigurationPort)

            # Validate updated configuration if provided
            validation_errors = []
            if command.configuration:
                validation_errors = template_port.validate_template_config(command.configuration)
                if validation_errors:
                    self.logger.warning(
                        "Template update validation failed for %s: %s",
                        command.template_id,
                        validation_errors,
                    )
                    command.validation_errors = validation_errors
                    command.updated = False
                    return

            # Update template through repository
            with self._uow_factory.create_unit_of_work() as uow:
                # Get existing template
                from orb.domain.template.value_objects import TemplateId

                template = uow.templates.get_by_id(TemplateId(value=command.template_id))
                if not template:
                    raise EntityNotFoundError("Template", command.template_id)

                # Track changes for event
                changes = {}

                # Update template properties
                if command.name is not None:
                    template = template.update_name(command.name)
                    changes["name"] = command.name

                if command.description is not None:
                    template = template.update_description(command.description)
                    changes["description"] = command.description

                if command.configuration:
                    template = template.update_configuration(command.configuration)
                    changes["configuration"] = command.configuration

                if command.instance_type is not None:
                    template = template.update_instance_type(command.instance_type)
                    changes["instance_type"] = command.instance_type

                if command.image_id is not None:
                    template = template.update_image_id(command.image_id)
                    changes["image_id"] = command.image_id

                # Save changes
                uow.templates.save(template)

                self.logger.info("Template updated successfully: %s", command.template_id)

            command.updated = True

        except EntityNotFoundError:
            self.logger.error("Template not found for update: %s", command.template_id)
            raise
        except Exception as e:
            self.logger.error("Failed to update template %s: %s", command.template_id, e)
            raise


@command_handler(DeleteTemplateCommand)  # type: ignore[arg-type]
class DeleteTemplateHandler(BaseCommandHandler[DeleteTemplateCommand, None]):  # type: ignore[type-var]
    """
    Handler for deleting templates.

    Responsibilities:
    - Validate template exists
    - Check if template is in use
    - Delete template through repository
    - Publish TemplateDeleted domain event

    CQRS Compliance: Returns None. Results stored in command.deleted.
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
            raise ValueError("template_id is required")

    async def execute_command(self, command: DeleteTemplateCommand) -> None:
        """Delete template with validation and events."""
        self.logger.info("Deleting template: %s", command.template_id)

        try:
            # Delete template through repository
            with self._uow_factory.create_unit_of_work() as uow:
                # Get existing template
                from orb.domain.template.value_objects import TemplateId

                template_id = TemplateId(value=command.template_id)
                template = uow.templates.get_by_id(template_id)
                if not template:
                    raise EntityNotFoundError("Template", command.template_id)

                # Check if template is in use (business rule)
                # This could be expanded to check for active requests using this
                # template
                if hasattr(template, "is_in_use") and template.is_in_use():
                    raise BusinessRuleError(
                        f"Cannot delete template {command.template_id}: template is in use"
                    )

                # Delete template
                uow.templates.delete(template_id)

                self.logger.info("Template deleted successfully: %s", command.template_id)

            command.deleted = True

        except EntityNotFoundError:
            self.logger.error("Template not found for deletion: %s", command.template_id)
            raise
        except BusinessRuleError:
            self.logger.error(
                "Cannot delete template %s: business rule violation",
                command.template_id,
            )
            raise
        except Exception as e:
            self.logger.error("Failed to delete template %s: %s", command.template_id, e)
            raise
