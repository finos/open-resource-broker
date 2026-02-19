"""Utility command factory for creating non-CQRS command data structures."""

from typing import Any


# Simple data structures for utility commands that don't need full CQRS
class InitCommandData:
    """Data structure for init command."""

    def __init__(self, **kwargs: Any):
        self.interactive = kwargs.get("interactive", False)
        self.non_interactive = kwargs.get("non_interactive", False)
        self.scheduler = kwargs.get("scheduler")
        self.provider = kwargs.get("provider")
        self.region = kwargs.get("region")
        self.config_dir = kwargs.get("config_dir")
        self.force = kwargs.get("force", False)


class MCPServeCommandData:
    """Data structure for MCP serve command."""

    def __init__(self, **kwargs: Any):
        self.stdio = kwargs.get("stdio", False)
        self.port = kwargs.get("port")
        self.host = kwargs.get("host", "localhost")
        self.log_level = kwargs.get("log_level", "INFO")


class MCPToolsCommandData:
    """Data structure for MCP tools command."""

    def __init__(self, action: str, **kwargs: Any):
        self.action = action  # list, call, info
        self.tool_name = kwargs.get("tool_name")
        self.arguments = kwargs.get("arguments", {})


class MCPValidateCommandData:
    """Data structure for MCP validate command."""

    def __init__(self, **kwargs: Any):
        self.config_path = kwargs.get("config_path")
        self.strict = kwargs.get("strict", False)


class InfrastructureCommandData:
    """Data structure for infrastructure command."""

    def __init__(self, action: str, **kwargs: Any):
        self.action = action  # discover, show, validate
        self.provider = kwargs.get("provider")
        self.region = kwargs.get("region")
        self.detailed = kwargs.get("detailed", False)


class ProviderOperationCommandData:
    """Data structure for provider operation command."""

    def __init__(self, action: str, **kwargs: Any):
        self.action = action  # select, exec
        self.provider = kwargs.get("provider")
        self.operation = kwargs.get("operation")
        self.params = kwargs.get("params")


class TemplateUtilityCommandData:
    """Data structure for template utility command."""

    def __init__(self, action: str, **kwargs: Any):
        self.action = action  # refresh, generate
        self.provider = kwargs.get("provider")
        self.all_providers = kwargs.get("all_providers", False)
        self.provider_api = kwargs.get("provider_api")
        self.output_dir = kwargs.get("output_dir")


class StorageTestCommandData:
    """Data structure for storage test command."""

    def __init__(self, **kwargs: Any):
        self.storage_type = kwargs.get("storage_type")
        self.config_path = kwargs.get("config_path")
        self.verbose = kwargs.get("verbose", False)


class SystemServeCommandData:
    """Data structure for system serve command."""

    def __init__(self, **kwargs: Any):
        self.host = kwargs.get("host", "localhost")
        self.port = kwargs.get("port", 8000)
        self.reload = kwargs.get("reload", False)
        self.log_level = kwargs.get("log_level", "INFO")


class UtilityCommandFactory:
    """Factory for creating utility command data structures."""

    def create_init_command_data(self, **kwargs: Any) -> InitCommandData:
        """Create init command data structure."""
        return InitCommandData(**kwargs)

    def create_mcp_serve_command_data(self, **kwargs: Any) -> MCPServeCommandData:
        """Create MCP serve command data structure."""
        return MCPServeCommandData(**kwargs)

    def create_mcp_tools_command_data(self, action: str, **kwargs: Any) -> MCPToolsCommandData:
        """Create MCP tools command data structure."""
        return MCPToolsCommandData(action, **kwargs)

    def create_mcp_validate_command_data(self, **kwargs: Any) -> MCPValidateCommandData:
        """Create MCP validate command data structure."""
        return MCPValidateCommandData(**kwargs)

    def create_infrastructure_command_data(
        self, action: str, **kwargs: Any
    ) -> InfrastructureCommandData:
        """Create infrastructure command data structure."""
        return InfrastructureCommandData(action, **kwargs)

    def create_provider_operation_command_data(
        self, action: str, **kwargs: Any
    ) -> ProviderOperationCommandData:
        """Create provider operation command data structure."""
        return ProviderOperationCommandData(action, **kwargs)

    def create_template_utility_command_data(
        self, action: str, **kwargs: Any
    ) -> TemplateUtilityCommandData:
        """Create template utility command data structure."""
        return TemplateUtilityCommandData(action, **kwargs)

    def create_storage_test_command_data(self, **kwargs: Any) -> StorageTestCommandData:
        """Create storage test command data structure."""
        return StorageTestCommandData(**kwargs)

    def create_system_serve_command_data(self, **kwargs: Any) -> SystemServeCommandData:
        """Create system serve command data structure."""
        return SystemServeCommandData(**kwargs)