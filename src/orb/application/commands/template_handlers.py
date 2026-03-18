"""Template command handlers for CQRS pattern."""

from orb.application.base.handlers import BaseCommandHandler
from orb.application.decorators import command_handler
from orb.application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    UpdateTemplateCommand,
)
from orb.domain.base.exceptions import DuplicateError, EntityNotFoundError
from orb.domain.base.ports import (
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)
from orb.domain.base.ports.template_configuration_port import TemplateConfigurationPort
from orb.infrastructure.template.dtos import TemplateDTO


@command_handler(CreateTemplateCommand)  # type: ignore[arg-type]
class CreateTemplateHandler(BaseCommandHandler[CreateTemplateCommand, None]):  # type: ignore[type-var]
    """
    Handler for creating templates.

    Responsibilities:
    - Validate template configuration
    - Check for duplicate template IDs
    - Persist template through TemplateConfigurationManager
    - Publish TemplateCreated domain event

    CQRS Compliance: Returns None. Results stored in command.validation_errors and command.created.
    """

    def __init__(
        self,
        template_port: TemplateConfigurationPort,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize the instance."""
        super().__init__(logger, event_publisher, error_handler)
        self._template_port = template_port

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
        """Create new template via TemplateConfigurationManager."""
        self.logger.info("Creating template: %s", command.template_id)

        # Validate configuration first
        validation_errors = self._template_port.validate_template_config(
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

        template_manager = self._template_port.get_template_manager()

        # Duplicate check
        existing = await template_manager.get_template_by_id(command.template_id)
        if existing:
            raise DuplicateError(
                f"Template {command.template_id} already exists",
                details={"entity_type": "Template", "entity_id": command.template_id},
            )

        # Build DTO from command fields — configuration provides defaults, named fields win
        dto_fields = {
            **command.configuration,
            "template_id": command.template_id,
            "name": command.name or command.template_id,
            "description": command.description,
            "provider_api": command.provider_api,
            "image_id": command.image_id,
            "tags": command.tags,
        }
        # instance_type → machine_types (backward compat)
        if command.instance_type is not None and "machine_types" not in dto_fields:
            dto_fields["machine_types"] = {command.instance_type: 1}
        # Remove None values so TemplateDTO defaults apply
        dto_fields = {k: v for k, v in dto_fields.items() if v is not None}
        dto = TemplateDTO(**dto_fields)

        await template_manager.save_template(dto)
        self.logger.info("Template created successfully: %s", command.template_id)
        command.created = True


@command_handler(UpdateTemplateCommand)  # type: ignore[arg-type]
class UpdateTemplateHandler(BaseCommandHandler[UpdateTemplateCommand, None]):  # type: ignore[type-var]
    """
    Handler for updating templates.

    Responsibilities:
    - Validate template exists
    - Validate updated configuration
    - Persist changes through TemplateConfigurationManager
    - Publish TemplateUpdated domain event

    CQRS Compliance: Returns None. Results stored in command.validation_errors and command.updated.
    """

    def __init__(
        self,
        template_port: TemplateConfigurationPort,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._template_port = template_port

    async def validate_command(self, command: UpdateTemplateCommand) -> None:
        """Validate update template command."""
        await super().validate_command(command)
        if not command.template_id:
            raise ValueError("template_id is required")

    async def execute_command(self, command: UpdateTemplateCommand) -> None:
        """Update existing template via TemplateConfigurationManager."""
        self.logger.info("Updating template: %s", command.template_id)

        template_manager = self._template_port.get_template_manager()

        existing = await template_manager.get_template_by_id(command.template_id)
        if not existing:
            raise EntityNotFoundError("Template", command.template_id)

        # Apply updates onto the existing DTO
        from typing import Any

        update_fields: dict[str, Any] = {}

        # Apply configuration as field overrides first
        if command.configuration:
            valid_fields = TemplateDTO.model_fields.keys()
            update_fields.update(
                {k: v for k, v in command.configuration.items() if k in valid_fields}
            )

        # Named fields override configuration
        for field, value in {
            "name": command.name,
            "description": command.description,
            "image_id": command.image_id,
        }.items():
            if value is not None:
                update_fields[field] = value

        # instance_type → machine_types (backward compat)
        if command.instance_type is not None and "machine_types" not in update_fields:
            update_fields["machine_types"] = {command.instance_type: 1}

        if update_fields:
            updated = existing.model_copy(update=update_fields)
        else:
            updated = existing

        # Validate the fully-merged DTO (not the raw partial patch)
        validation_errors = self._template_port.validate_template_config(updated.model_dump())
        if validation_errors:
            self.logger.warning(
                "Template update validation failed for %s: %s",
                command.template_id,
                validation_errors,
            )
            command.validation_errors = validation_errors
            command.updated = False
            return

        await template_manager.save_template(updated)
        self.logger.info("Template updated successfully: %s", command.template_id)
        command.updated = True


@command_handler(DeleteTemplateCommand)  # type: ignore[arg-type]
class DeleteTemplateHandler(BaseCommandHandler[DeleteTemplateCommand, None]):  # type: ignore[type-var]
    """
    Handler for deleting templates.

    Responsibilities:
    - Validate template exists
    - Delete template through TemplateConfigurationManager
    - Publish TemplateDeleted domain event

    CQRS Compliance: Returns None. Results stored in command.deleted.
    """

    def __init__(
        self,
        template_port: TemplateConfigurationPort,
        logger: LoggingPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        super().__init__(logger, event_publisher, error_handler)
        self._template_port = template_port

    async def validate_command(self, command: DeleteTemplateCommand) -> None:
        """Validate delete template command."""
        await super().validate_command(command)
        if not command.template_id:
            raise ValueError("template_id is required")

    async def execute_command(self, command: DeleteTemplateCommand) -> None:
        """Delete template via TemplateConfigurationManager."""
        self.logger.info("Deleting template: %s", command.template_id)

        template_manager = self._template_port.get_template_manager()

        existing = await template_manager.get_template_by_id(command.template_id)
        if not existing:
            raise EntityNotFoundError("Template", command.template_id)

        await template_manager.delete_template(command.template_id)
        self.logger.info("Template deleted successfully: %s", command.template_id)
        command.deleted = True
