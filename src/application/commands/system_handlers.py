"""System command handlers for administrative operations."""

from typing import Any, Dict

from src.application.base.handlers import BaseCommandHandler
from src.application.commands.system import ReloadProviderConfigCommand
from src.application.decorators import command_handler
from src.domain.base.ports import (
    ContainerPort,
    ErrorHandlingPort,
    EventPublisherPort,
    LoggingPort,
)

# ============================================================================
# Provider Configuration Management Handlers
# ============================================================================


@command_handler(ReloadProviderConfigCommand)
class ReloadProviderConfigHandler(BaseCommandHandler[ReloadProviderConfigCommand, Dict[str, Any]]):
    """Handler for reloading provider configuration."""

    def __init__(
        self,
        logger: LoggingPort,
        container: ContainerPort,
        event_publisher: EventPublisherPort,
        error_handler: ErrorHandlingPort,
    ):
        """Initialize reload provider config handler."""
        super().__init__(logger, event_publisher, error_handler)
        self.container = container

    async def validate_command(self, command: ReloadProviderConfigCommand) -> None:
        """Validate reload provider config command."""
        await super().validate_command(command)

    async def execute_command(self, command: ReloadProviderConfigCommand) -> Dict[str, Any]:
        """Execute provider configuration reload command."""
        self.logger.info(
            f"Reloading provider configuration from: {command.config_path or 'default location'}"
        )

        try:
            # Get configuration manager from container
            from src.domain.base.ports import ConfigurationPort

            config_manager = self.container.get(ConfigurationPort)

            # Reload configuration (implementation depends on ConfigurationManager
            # capabilities)
            if hasattr(config_manager, "reload"):
                config_manager.reload(command.config_path)
            else:
                # Fallback: create new configuration manager instance using factory
                from src.config.manager import get_config_manager

                get_config_manager(command.config_path)

            # Get updated provider information
            if hasattr(config_manager, "get_provider_config"):
                provider_config = config_manager.get_provider_config()
                if provider_config:
                    provider_mode = provider_config.get_mode().value
                    active_providers = [
                        p.name for p in provider_config.get_active_providers()]
                else:
                    provider_mode = "strategy"
                    active_providers = []
            else:
                provider_mode = "strategy"
                active_providers = []

            result = {
                "status": "success",
                "message": "Provider configuration reloaded successfully",
                "config_path": command.config_path,
                "provider_mode": provider_mode,
                "active_providers": active_providers,
                "command_id": command.command_id,
            }

            self.logger.info("Provider configuration reload completed successfully")
            return result

        except Exception as e:
            self.logger.error(f"Provider configuration reload failed: {str(e)}")
            return {
                "status": "failed",
                "error": str(e),
                "command_id": command.command_id,
                "config_path": command.config_path,
            }
