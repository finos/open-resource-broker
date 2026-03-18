"""CLI command factory orchestrator for coordinating focused command factories."""

from typing import Any

from .machine_command_factory import MachineCommandFactory
from .provider_command_factory import ProviderCommandFactory
from .request_command_factory import RequestCommandFactory
from .scheduler_command_factory import SchedulerCommandFactory
from .storage_command_factory import StorageCommandFactory
from .system_command_factory import SystemCommandFactory
from .template_command_factory import TemplateCommandFactory
from .utility_command_factory import UtilityCommandFactory


class CLICommandFactoryOrchestrator:
    """Orchestrates focused command factories for CLI operations."""

    def __init__(self):
        self._template_factory = TemplateCommandFactory()
        self._request_factory = RequestCommandFactory()
        self._machine_factory = MachineCommandFactory()
        self._system_factory = SystemCommandFactory()
        self._provider_factory = ProviderCommandFactory()
        self._storage_factory = StorageCommandFactory()
        self._scheduler_factory = SchedulerCommandFactory()
        self._utility_factory = UtilityCommandFactory()

    # Template operations
    def create_list_templates_query(self, **kwargs: Any):
        """Create query to list templates."""
        return self._template_factory.create_list_templates_query(**kwargs)

    def create_get_template_query(self, **kwargs: Any):
        """Create query to get template by ID."""
        return self._template_factory.create_get_template_query(**kwargs)

    def create_create_template_command(self, **kwargs: Any):
        """Create command to create template."""
        return self._template_factory.create_create_template_command(**kwargs)

    def create_update_template_command(self, **kwargs: Any):
        """Create command to update template."""
        return self._template_factory.create_update_template_command(**kwargs)

    def create_delete_template_command(self, **kwargs: Any):
        """Create command to delete template."""
        return self._template_factory.create_delete_template_command(**kwargs)

    def create_validate_template_query(self, **kwargs: Any):
        """Create query to validate template configuration."""
        return self._template_factory.create_validate_template_query(**kwargs)

    def create_get_multiple_templates_query(self, **kwargs: Any):
        """Create query to get multiple templates by IDs."""
        return self._template_factory.create_get_multiple_templates_query(**kwargs)

    # Request operations
    def create_create_request_command(self, **kwargs: Any):
        """Create command to create machine request."""
        return self._request_factory.create_create_request_command(**kwargs)

    def create_get_request_status_query(self, **kwargs: Any):
        """Create query to get request status."""
        return self._request_factory.create_get_request_status_query(**kwargs)

    def create_list_requests_query(self, **kwargs: Any):
        """Create query to list requests."""
        return self._request_factory.create_list_requests_query(**kwargs)

    def create_cancel_request_command(self, **kwargs: Any):
        """Create command to cancel request."""
        return self._request_factory.create_cancel_request_command(**kwargs)

    def create_return_request_command(self, **kwargs: Any):
        """Create command to return machines."""
        return self._request_factory.create_return_request_command(**kwargs)

    def create_list_return_requests_query(self, **kwargs: Any):
        """Create query to list return requests."""
        return self._request_factory.create_list_return_requests_query(**kwargs)

    def create_list_active_requests_query(self, **kwargs: Any):
        """Create query to list active requests."""
        return self._request_factory.create_list_active_requests_query(**kwargs)

    def create_get_multiple_requests_query(self, **kwargs: Any):
        """Create query to get multiple requests by IDs."""
        return self._request_factory.create_get_multiple_requests_query(**kwargs)

    # Machine operations
    def create_list_machines_query(self, **kwargs: Any):
        """Create query to list machines."""
        return self._machine_factory.create_list_machines_query(**kwargs)

    def create_get_machine_query(self, **kwargs: Any):
        """Create query to get machine by ID."""
        return self._machine_factory.create_get_machine_query(**kwargs)

    def create_update_machine_status_command(self, **kwargs: Any):
        """Create command to update machine status."""
        return self._machine_factory.create_update_machine_status_command(**kwargs)

    def create_get_multiple_machines_query(self, **kwargs: Any):
        """Create query to get multiple machines by IDs."""
        return self._machine_factory.create_get_multiple_machines_query(**kwargs)

    # System operations
    def create_reload_provider_config_command(self, **kwargs: Any):
        """Create command to reload provider configuration."""
        return self._system_factory.create_reload_provider_config_command(**kwargs)

    def create_refresh_templates_command(self, **kwargs: Any):
        """Create command to refresh templates."""
        return self._system_factory.create_refresh_templates_command(**kwargs)

    def create_get_system_status_query(self, **kwargs: Any):
        """Create query to get system status."""
        return self._system_factory.create_get_system_status_query(**kwargs)

    def create_get_provider_config_query(self, **kwargs: Any):
        """Create query to get provider configuration."""
        return self._system_factory.create_get_provider_config_query(**kwargs)

    def create_get_system_config_query(self, **kwargs: Any):
        """Create query to get full system configuration."""
        return self._system_factory.create_get_system_config_query(**kwargs)

    def create_get_provider_metrics_query(self, **kwargs: Any):
        """Create query to get provider metrics."""
        return self._system_factory.create_get_provider_metrics_query(**kwargs)

    def create_validate_provider_config_query(self, **kwargs: Any):
        """Create query to validate provider configuration."""
        return self._system_factory.create_validate_provider_config_query(**kwargs)

    def create_test_storage_query(self, **kwargs: Any):
        """Create query to test storage."""
        return self._system_factory.create_test_storage_query(**kwargs)

    def create_mcp_validate_query(self, **kwargs: Any):
        """Create query to validate MCP."""
        return self._system_factory.create_mcp_validate_query(**kwargs)

    def create_get_configuration_query(self, **kwargs: Any):
        """Create query to get configuration value."""
        return self._system_factory.create_get_configuration_query(**kwargs)

    def create_set_configuration_command(self, **kwargs: Any):
        """Create command to set configuration value."""
        return self._system_factory.create_set_configuration_command(**kwargs)

    # Provider operations
    def create_get_provider_health_query(self, **kwargs: Any):
        """Create query to get provider health."""
        return self._provider_factory.create_get_provider_health_query(**kwargs)

    def create_list_available_providers_query(self, **kwargs: Any):
        """Create query to list available providers."""
        return self._provider_factory.create_list_available_providers_query(**kwargs)

    def create_get_provider_capabilities_query(self, **kwargs: Any):
        """Create query to get provider capabilities."""
        return self._provider_factory.create_get_provider_capabilities_query(**kwargs)

    def create_get_provider_strategy_config_query(self, **kwargs: Any):
        """Create query to get provider strategy configuration."""
        return self._provider_factory.create_get_provider_strategy_config_query(**kwargs)

    def create_execute_provider_operation_command(self, **kwargs: Any):
        """Create command to execute provider operation."""
        return self._provider_factory.create_execute_provider_operation_command(**kwargs)

    # Storage operations
    def create_list_storage_strategies_query(self, **kwargs: Any):
        """Create query to list storage strategies."""
        return self._storage_factory.create_list_storage_strategies_query(**kwargs)

    def create_get_storage_health_query(self, **kwargs: Any):
        """Create query to get storage health."""
        return self._storage_factory.create_get_storage_health_query(**kwargs)

    def create_get_storage_metrics_query(self, **kwargs: Any):
        """Create query to get storage metrics."""
        return self._storage_factory.create_get_storage_metrics_query(**kwargs)

    # Scheduler operations
    def create_list_scheduler_strategies_query(self, **kwargs: Any):
        """Create query to list scheduler strategies."""
        return self._scheduler_factory.create_list_scheduler_strategies_query(**kwargs)

    def create_get_scheduler_configuration_query(self, **kwargs: Any):
        """Create query to get scheduler configuration."""
        return self._scheduler_factory.create_get_scheduler_configuration_query(**kwargs)

    def create_validate_scheduler_configuration_query(self, **kwargs: Any):
        """Create query to validate scheduler configuration."""
        return self._scheduler_factory.create_validate_scheduler_configuration_query(**kwargs)

    # Utility command data structures
    def create_init_command_data(self, **kwargs: Any):
        """Create init command data structure."""
        return self._utility_factory.create_init_command_data(**kwargs)

    def create_mcp_serve_command_data(self, **kwargs: Any):
        """Create MCP serve command data structure."""
        return self._utility_factory.create_mcp_serve_command_data(**kwargs)

    def create_mcp_tools_command_data(self, action: str, **kwargs: Any):
        """Create MCP tools command data structure."""
        # Remove action from kwargs to avoid duplicate argument
        filtered_kwargs = {k: v for k, v in kwargs.items() if k != "action"}
        return self._utility_factory.create_mcp_tools_command_data(action, **filtered_kwargs)

    def create_mcp_validate_command_data(self, **kwargs: Any):
        """Create MCP validate command data structure."""
        return self._utility_factory.create_mcp_validate_command_data(**kwargs)

    def create_infrastructure_command_data(self, action: str, **kwargs: Any):
        """Create infrastructure command data structure."""
        # Remove action from kwargs to avoid duplicate argument
        filtered_kwargs = {k: v for k, v in kwargs.items() if k != "action"}
        return self._utility_factory.create_infrastructure_command_data(action, **filtered_kwargs)

    def create_provider_operation_command_data(self, action: str, **kwargs: Any):
        """Create provider operation command data structure."""
        # Remove action from kwargs to avoid duplicate argument
        filtered_kwargs = {k: v for k, v in kwargs.items() if k != "action"}
        return self._utility_factory.create_provider_operation_command_data(
            action, **filtered_kwargs
        )

    def create_template_utility_command_data(self, action: str, **kwargs: Any):
        """Create template utility command data structure."""
        # Remove action from kwargs to avoid duplicate argument
        filtered_kwargs = {k: v for k, v in kwargs.items() if k != "action"}
        return self._utility_factory.create_template_utility_command_data(action, **filtered_kwargs)

    def create_storage_test_command_data(self, **kwargs: Any):
        """Create storage test command data structure."""
        return self._utility_factory.create_storage_test_command_data(**kwargs)

    def create_system_serve_command_data(self, **kwargs: Any):
        """Create system serve command data structure."""
        return self._utility_factory.create_system_serve_command_data(**kwargs)
