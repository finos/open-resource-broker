"""CLI command factory orchestrator for coordinating focused command factories."""

from typing import Any

from .template_command_factory import TemplateCommandFactory
from .request_command_factory import RequestCommandFactory
from .machine_command_factory import MachineCommandFactory
from .system_command_factory import SystemCommandFactory
from .provider_command_factory import ProviderCommandFactory
from .storage_command_factory import StorageCommandFactory
from .scheduler_command_factory import SchedulerCommandFactory
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

    def create_validate_template_command(self, **kwargs: Any):
        """Create command to validate template."""
        return self._template_factory.create_validate_template_command(**kwargs)

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

    def create_get_provider_metrics_query(self, **kwargs: Any):
        """Create query to get provider metrics."""
        return self._system_factory.create_get_provider_metrics_query(**kwargs)

    def create_validate_provider_config_query(self, **kwargs: Any):
        """Create query to validate provider configuration."""
        return self._system_factory.create_validate_provider_config_query(**kwargs)

    def create_test_storage_command(self, **kwargs: Any):
        """Create command to test storage."""
        return self._system_factory.create_test_storage_command(**kwargs)

    def create_mcp_validate_command(self, **kwargs: Any):
        """Create command to validate MCP."""
        return self._system_factory.create_mcp_validate_command(**kwargs)

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
        return self._utility_factory.create_mcp_tools_command_data(action, **kwargs)

    def create_mcp_validate_command_data(self, **kwargs: Any):
        """Create MCP validate command data structure."""
        return self._utility_factory.create_mcp_validate_command_data(**kwargs)

    def create_infrastructure_command_data(self, action: str, **kwargs: Any):
        """Create infrastructure command data structure."""
        return self._utility_factory.create_infrastructure_command_data(action, **kwargs)

    def create_provider_operation_command_data(self, action: str, **kwargs: Any):
        """Create provider operation command data structure."""
        return self._utility_factory.create_provider_operation_command_data(action, **kwargs)

    def create_template_utility_command_data(self, action: str, **kwargs: Any):
        """Create template utility command data structure."""
        return self._utility_factory.create_template_utility_command_data(action, **kwargs)

    def create_storage_test_command_data(self, **kwargs: Any):
        """Create storage test command data structure."""
        return self._utility_factory.create_storage_test_command_data(**kwargs)

    def create_system_serve_command_data(self, **kwargs: Any):
        """Create system serve command data structure."""
        return self._utility_factory.create_system_serve_command_data(**kwargs)

    def create_command_or_query(self, args):
        """Create appropriate Command or Query from CLI arguments."""
        # Process input data from -f/--file or -d/--data flags (HostFactory compatibility)
        input_data = self._process_input_data(args)

        # Convert args to dict for easier processing
        args_dict = vars(args).copy()
        args_dict["input_data"] = input_data

        # Handle positional vs flag arguments for template_id
        if hasattr(args, "template_id") and args.template_id:
            args_dict["template_id"] = args.template_id
        elif hasattr(args, "flag_template_id") and args.flag_template_id:
            args_dict["template_id"] = args.flag_template_id

        # Handle positional vs flag arguments for machine_id
        if hasattr(args, "machine_id") and args.machine_id:
            args_dict["machine_id"] = args.machine_id
        elif hasattr(args, "flag_machine_id") and args.flag_machine_id:
            args_dict["machine_id"] = args.flag_machine_id

        # Handle positional vs flag arguments for request_id
        if hasattr(args, "request_id") and args.request_id:
            args_dict["request_id"] = args.request_id
        elif hasattr(args, "flag_request_id") and args.flag_request_id:
            args_dict["request_id"] = args.flag_request_id

        # Handle multiple request IDs (both positional and flag)
        request_ids = []
        if hasattr(args, "request_ids") and args.request_ids:
            request_ids.extend(args.request_ids)
        if hasattr(args, "flag_request_ids") and args.flag_request_ids:
            request_ids.extend(args.flag_request_ids)
        if request_ids:
            args_dict["request_ids"] = request_ids

        # Normalize machine_count arguments
        if "machine_count" in args_dict and args_dict["machine_count"] is not None:
            args_dict["count"] = args_dict["machine_count"]
        elif "flag_machine_count" in args_dict and args_dict["flag_machine_count"] is not None:
            args_dict["count"] = args_dict["flag_machine_count"]

        # Extract command group and action
        command_group = getattr(args, "resource", None)
        command_action = getattr(args, "action", None)

        # Route to appropriate factory based on command group
        return self._route_command(command_group, command_action, args_dict)

    def _route_command(self, command_group: str, command_action: str, args: dict):
        """Route command to appropriate factory method."""
        # Template operations
        if command_group in ["templates", "template"]:
            if command_action == "list":
                return self.create_list_templates_query(
                    provider_name=args.get("provider"),
                    active_only=not args.get("all", False),
                    include_details=args.get("long", False),
                    filter_expressions=args.get("filter") or [],
                )
            elif command_action == "show":
                return self.create_get_template_query(
                    template_id=args.get("template_id"), provider=args.get("provider")
                )
            elif command_action == "create":
                return self.create_create_template_command(
                    template_id=args.get("template_id"),
                    provider_name=args.get("provider"),
                    handler_type=args.get("handler_type"),
                    configuration=args.get("configuration", {}),
                    description=args.get("description"),
                    tags=args.get("tags", {}),
                )
            elif command_action == "update":
                return self.create_update_template_command(
                    template_id=args.get("template_id"),
                    configuration=args.get("configuration"),
                    description=args.get("description"),
                    tags=args.get("tags"),
                )
            elif command_action == "delete":
                return self.create_delete_template_command(template_id=args.get("template_id"))
            elif command_action == "validate":
                return self.create_validate_template_command(template_id=args.get("template_id"))
            elif command_action == "refresh":
                return self.create_template_utility_command_data("refresh", **args)
            elif command_action == "generate":
                return self.create_template_utility_command_data("generate", **args)

        # Request operations
        elif command_group in ["requests", "request"]:
            if command_action == "create":
                return self.create_create_request_command(
                    template_id=args.get("template_id"),
                    count=args.get("count", 1),
                    provider=args.get("provider"),
                )
            elif command_action == "status":
                # Return None to trigger fallback to scheduler-aware handler
                return None
            elif command_action == "list":
                return self.create_list_requests_query(
                    provider=args.get("provider"),
                    status=args.get("status"),
                    limit=args.get("limit"),
                )
            elif command_action == "cancel":
                return self.create_cancel_request_command(request_id=args.get("request_id"))
            elif command_action == "return":
                return self.create_return_request_command(
                    machine_ids=args.get("machine_ids", []), reason=args.get("reason")
                )

        # Machine operations
        elif command_group in ["machines", "machine"]:
            if command_action == "list":
                # Return None to trigger fallback to scheduler-aware handler
                return None
            elif command_action == "show":
                return self.create_get_machine_query(machine_id=args.get("machine_id"))
            elif command_action == "request":
                return self.create_create_request_command(
                    template_id=args.get("template_id"),
                    count=args.get("machine_count") or args.get("flag_machine_count", 1),
                    provider=args.get("provider"),
                )

        # System operations
        elif command_group == "system":
            if command_action == "status":
                return self.create_get_system_status_query(
                    include_health=True,
                    include_metrics=args.get("detailed", False),
                    include_config=args.get("detailed", False),
                )
            elif command_action == "reload":
                return self.create_reload_provider_config_command(config_path=args.get("file"))

        # Provider operations
        elif command_group in ["providers", "provider"]:
            if command_action == "list":
                return self.create_list_available_providers_query(
                    include_health=True,
                    include_capabilities=args.get("detailed", False),
                    include_metrics=args.get("detailed", False),
                    filter_expressions=args.get("filter") or [],
                    provider_name=args.get("provider"),
                )
            elif command_action == "show":
                provider_name = args.get("provider")
                if provider_name:
                    return self.create_get_provider_capabilities_query(provider_name=provider_name)
                else:
                    return self.create_list_available_providers_query(
                        include_health=True, include_capabilities=True, include_metrics=True
                    )
            elif command_action == "health":
                return self.create_get_provider_health_query(
                    provider_name=args.get("provider"), include_details=True
                )
            elif command_action == "metrics":
                return self.create_get_provider_metrics_query(
                    provider_name=args.get("provider"),
                    timeframe=args.get("timeframe", "1h"),
                    detailed=args.get("detailed", False),
                )
            elif command_action == "select":
                filtered_args = {k: v for k, v in args.items() if k != "action"}
                return self.create_provider_operation_command_data("select", **filtered_args)
            elif command_action == "exec":
                return self.create_execute_provider_operation_command(
                    operation=args.get("operation"),
                    params=args.get("params"),
                    provider_name=args.get("provider"),
                )

        # Storage operations
        elif command_group == "storage":
            if command_action == "list":
                return self.create_list_storage_strategies_query(
                    include_current=True,
                    include_details=args.get("detailed", False),
                    filter_expressions=args.get("filter") or [],
                )
            elif command_action == "health":
                return self.create_get_storage_health_query(
                    strategy_name=args.get("storage"), detailed=True
                )
            elif command_action == "metrics":
                return self.create_get_storage_metrics_query(
                    strategy_name=args.get("storage"),
                    time_range=args.get("timeframe", "1h"),
                    include_operations=args.get("detailed", False),
                )
            elif command_action == "test":
                return self.create_storage_test_command_data(**args)

        # Scheduler operations
        elif command_group == "scheduler":
            if command_action == "list":
                return self.create_list_scheduler_strategies_query(
                    include_current=True,
                    include_details=args.get("long", False),
                    filter_expressions=args.get("filter") or [],
                )
            elif command_action == "show":
                return self.create_get_scheduler_configuration_query(
                    scheduler_name=args.get("scheduler")
                )
            elif command_action == "validate":
                return self.create_validate_scheduler_configuration_query(
                    scheduler_name=args.get("scheduler")
                )

        # Infrastructure operations
        elif command_group in {"infrastructure", "infra"}:
            if command_action == "discover":
                return self.create_infrastructure_command_data(
                    "discover", **{k: v for k, v in args.items() if k != "action"}
                )
            elif command_action == "show":
                return self.create_infrastructure_command_data(
                    "show", **{k: v for k, v in args.items() if k != "action"}
                )
            elif command_action == "validate":
                return self.create_infrastructure_command_data(
                    "validate", **{k: v for k, v in args.items() if k != "action"}
                )

        # MCP operations
        elif command_group == "mcp":
            if command_action == "serve":
                return self.create_mcp_serve_command_data(**args)
            elif command_action == "tools":
                tools_action = args.get("tools_action")
                return self.create_mcp_tools_command_data(
                    tools_action,
                    **{k: v for k, v in args.items() if k not in ["action", "tools_action"]},
                )
            elif command_action == "validate":
                return self.create_mcp_validate_command()

        # Init command
        elif command_group == "init":
            return self.create_init_command_data(**args)

        # Config operations (map to provider config)
        elif command_group == "config":
            if command_action == "show":
                return self.create_get_provider_config_query(
                    provider_name=args.get("provider"), include_sensitive=False
                )
            elif command_action == "get":
                return self.create_get_configuration_query(
                    key=args.get("key"), default=args.get("default")
                )
            elif command_action == "set":
                return self.create_set_configuration_command(
                    key=args.get("key"), value=args.get("value")
                )
            elif command_action == "validate":
                return self.create_validate_provider_config_query(detailed=True)
            elif command_action == "reload":
                return self.create_reload_provider_config_command(config_path=args.get("file"))

        # For commands not yet converted to CQRS, return None
        # This allows the execute_command function to fall back to legacy handlers
        return None

    def _process_input_data(self, args):
        """Process input data from -f/--file or -d/--data flags."""
        # This method would handle file/data processing
        # For now, return None as placeholder
        return None
