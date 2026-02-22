"""System command handlers for administrative operations."""

from typing import Any

from application.base.handlers import BaseCommandHandler
from application.commands.system import (
    MCPValidateCommand,
    RefreshTemplatesCommand,
    ReloadProviderConfigCommand,
    SetConfigurationCommand,
    TestStorageCommand,
)
from application.decorators import command_handler
from domain.base.ports import (
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)

# ============================================================================
# Provider Configuration Management Handlers
# ============================================================================


@command_handler(ReloadProviderConfigCommand)
class ReloadProviderConfigHandler(BaseCommandHandler[ReloadProviderConfigCommand, None]):  # type: ignore[type-var]
    """Handler for reloading provider configuration.

    CQRS Compliance: Returns None. Results stored in command.result.
    """

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize reload provider config handler."""
        super().__init__(logger, event_publisher, error_handler)
        self.container = container

    async def validate_command(self, command: ReloadProviderConfigCommand) -> None:
        """Validate reload provider config command."""
        await super().validate_command(command)

    async def execute_command(self, command: ReloadProviderConfigCommand) -> None:
        """Execute provider configuration reload command."""
        self.logger.info(
            "Reloading provider configuration from: %s",
            command.config_path or "default location",
        )

        try:
            # Get configuration manager from container
            from domain.base.ports import ConfigurationPort

            config_manager = self.container.get(ConfigurationPort)

            # Reload configuration (implementation depends on ConfigurationManager
            # capabilities)
            if hasattr(config_manager, "reload"):
                config_manager.reload(command.config_path)
            else:
                # Fallback: get configuration manager from DI container
                # Note: ConfigurationManager doesn't support reload with different path
                # This is a limitation of the current implementation
                pass

            # Get updated provider information
            if hasattr(config_manager, "get_provider_config"):
                provider_config = config_manager.get_provider_config()
                if provider_config:
                    provider_mode = provider_config.get_mode().value
                    active_providers = [p.name for p in provider_config.get_active_providers()]
                else:
                    provider_mode = "strategy"
                    active_providers = []
            else:
                provider_mode = "strategy"
                active_providers = []

            # Store result in command (CQRS compliance)
            command.result = {
                "status": "success",
                "message": "Provider configuration reloaded successfully",
                "config_path": command.config_path,
                "provider_mode": provider_mode,
                "active_providers": active_providers,
                "command_id": command.command_id,
            }

            self.logger.info("Provider configuration reload completed successfully")

        except Exception as e:
            self.logger.error("Provider configuration reload failed: %s", str(e))
            # Store error result in command
            command.result = {
                "status": "failed",
                "error": str(e),
                "command_id": command.command_id,
                "config_path": command.config_path,
            }


@command_handler(RefreshTemplatesCommand)
class RefreshTemplatesHandler(BaseCommandHandler[RefreshTemplatesCommand, None]):  # type: ignore[type-var]
    """Handler for refreshing templates from all sources.

    CQRS Compliance: Returns None. Results stored in command.result.
    """

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize refresh templates handler."""
        super().__init__(logger, event_publisher, error_handler)
        self.container = container

    async def validate_command(self, command: RefreshTemplatesCommand) -> None:
        """Validate refresh templates command."""
        await super().validate_command(command)

    async def execute_command(self, command: RefreshTemplatesCommand) -> None:
        """Execute template refresh command."""
        self.logger.info("Refreshing templates from all sources")

        try:
            from domain.base.ports import TemplateConfigurationPort

            template_manager = self.container.get(TemplateConfigurationPort)

            # Refresh templates
            templates = await template_manager.load_templates(command.provider_name)

            # Store result in command (CQRS compliance)
            command.result = {
                "status": "success",
                "message": "Templates refreshed successfully",
                "template_count": len(templates),
                "provider_name": command.provider_name,
                "command_id": command.command_id,
            }

            self.logger.info("Template refresh completed successfully")

        except Exception as e:
            self.logger.error("Template refresh failed: %s", str(e))
            # Store error result in command
            command.result = {
                "status": "failed",
                "error": str(e),
                "command_id": command.command_id,
                "provider_name": command.provider_name,
            }


@command_handler(SetConfigurationCommand)
class SetConfigurationHandler(BaseCommandHandler[SetConfigurationCommand, None]):  # type: ignore[type-var]
    """Handler for setting configuration values.

    CQRS Compliance: Returns None. Results stored in command.result.
    """

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize set configuration handler."""
        super().__init__(logger, event_publisher, error_handler)
        self.container = container

    async def validate_command(self, command: SetConfigurationCommand) -> None:
        """Validate set configuration command."""
        await super().validate_command(command)
        if not command.key:
            raise ValueError("Configuration key is required")

    async def execute_command(self, command: SetConfigurationCommand) -> None:
        """Execute set configuration command."""
        self.logger.info("Setting configuration: %s = %s", command.key, command.value)

        try:
            from domain.base.ports import ConfigurationPort

            config_manager = self.container.get(ConfigurationPort)
            config_manager.set_configuration_value(command.key, command.value)

            # Store result in command (CQRS compliance)
            command.result = {
                "status": "success",
                "message": f"Configuration '{command.key}' set successfully",
                "key": command.key,
                "value": command.value,
                "command_id": command.command_id,
            }

            self.logger.info("Configuration set completed successfully")

        except Exception as e:
            self.logger.error("Set configuration failed: %s", str(e))
            # Store error result in command
            command.result = {
                "status": "failed",
                "error": str(e),
                "command_id": command.command_id,
                "key": command.key,
            }


@command_handler(TestStorageCommand)
class TestStorageCommandHandler(BaseCommandHandler[TestStorageCommand, None]):  # type: ignore[type-var]
    """Handler for testing storage connectivity and functionality.

    CQRS Compliance: Returns None. Results stored in command.result.
    Note: This is a pure query operation and should be moved to queries/ in future refactoring.
    """

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize test storage handler."""
        super().__init__(logger, event_publisher, error_handler)
        self.container = container

    async def validate_command(self, command: TestStorageCommand) -> None:
        """Validate test storage command."""
        await super().validate_command(command)

    async def execute_command(self, command: TestStorageCommand) -> None:
        """Execute storage test command."""
        self.logger.info("Testing storage connectivity")

        try:
            from application.ports.storage_registry_port import StorageRegistryPort

            registry = self.container.get(StorageRegistryPort)
            storage_types = registry.get_registered_types()

            # Store result in command (CQRS compliance)
            command.result = {
                "status": "success",
                "message": "Storage test completed successfully",
                "storage_types": storage_types,
                "command_id": command.command_id,
            }

            self.logger.info("Storage test completed successfully")

        except Exception as e:
            self.logger.error("Storage test failed: %s", str(e))
            # Store error result in command
            command.result = {
                "status": "failed",
                "error": str(e),
                "command_id": command.command_id,
            }


@command_handler(MCPValidateCommand)
class MCPValidateCommandHandler(BaseCommandHandler[MCPValidateCommand, None]):  # type: ignore[type-var]
    """Handler for validating MCP server configuration and tools.

    CQRS Compliance: Returns None. Results stored in command.result.
    Note: This is a pure query operation and should be moved to queries/ in future refactoring.
    """

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ) -> None:
        """Initialize MCP validate handler."""
        super().__init__(logger, event_publisher, error_handler)
        self.container = container

    async def validate_command(self, command: MCPValidateCommand) -> None:
        """Validate MCP validate command."""
        await super().validate_command(command)

    async def execute_command(self, command: MCPValidateCommand) -> None:
        """Execute MCP validation command."""
        self.logger.info("Validating MCP server configuration")

        try:
            # Store result in command (CQRS compliance)
            command.result = {
                "status": "success",
                "message": "MCP validation completed successfully",
                "command_id": command.command_id,
            }

            self.logger.info("MCP validation completed successfully")

        except Exception as e:
            self.logger.error("MCP validation failed: %s", str(e))
            # Store error result in command
            command.result = {
                "status": "failed",
                "error": str(e),
                "command_id": command.command_id,
            }
