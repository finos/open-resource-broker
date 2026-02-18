"""CLI Command Factory - Maps CLI arguments to Commands/Queries."""

from typing import Any, Dict, List, Optional, Union

from application.dto.commands import (
    CancelRequestCommand,
    CreateRequestCommand,
    CreateReturnRequestCommand,
    UpdateRequestStatusCommand,
)
from application.dto.queries import (
    GetConfigurationQuery,
    GetMachineQuery,
    GetRequestQuery,
    GetTemplateQuery,
    ListActiveRequestsQuery,
    ListReturnRequestsQuery,
    ListTemplatesQuery,
    ValidateTemplateQuery,
)
from application.machine.queries import ListMachinesQuery
from application.machine.commands import (
    RegisterMachineCommand,
    UpdateMachineStatusCommand,
)
from application.request.queries import (
    GetRequestHistoryQuery,
    ListRequestsQuery,
)
from application.provider.commands import ExecuteProviderOperationCommand
from application.template.commands import (
    CreateTemplateCommand,
    DeleteTemplateCommand,
    UpdateTemplateCommand,
    ValidateTemplateCommand,
)
from application.commands.system import (
    ReloadProviderConfigCommand,
    TestStorageCommand,
    MCPValidateCommand,
    RefreshTemplatesCommand,
    SetConfigurationCommand,
)
from application.queries.system import (
    GetSystemStatusQuery,
    GetProviderConfigQuery,
    GetProviderMetricsQuery,
    ValidateProviderConfigQuery,
)
from application.queries.storage import (
    ListStorageStrategiesQuery,
    GetStorageHealthQuery,
    GetStorageMetricsQuery,
)
from application.queries.scheduler import (
    ListSchedulerStrategiesQuery,
    GetSchedulerConfigurationQuery,
    ValidateSchedulerConfigurationQuery,
)
from application.provider.queries import (
    GetProviderHealthQuery,
    ListAvailableProvidersQuery,
    GetProviderCapabilitiesQuery,
    GetProviderMetricsQuery as ProviderMetricsQuery,
    GetProviderStrategyConfigQuery,
)
from domain.request.value_objects import RequestStatus


# Simple data structures for utility commands that don't need full CQRS
class InitCommandData:
    """Data structure for init command."""

    def __init__(self, **kwargs):
        self.non_interactive = kwargs.get("non_interactive", False)
        self.force = kwargs.get("force", False)
        self.scheduler = kwargs.get("scheduler")
        self.provider = kwargs.get("provider", "aws")
        self.region = kwargs.get("region")
        self.profile = kwargs.get("profile")
        self.config_dir = kwargs.get("config_dir")


class MCPServeCommandData:
    """Data structure for MCP serve command."""

    def __init__(self, **kwargs):
        self.port = kwargs.get("port", 3000)
        self.host = kwargs.get("host", "localhost")
        self.stdio = kwargs.get("stdio", False)
        self.log_level = kwargs.get("log_level", "INFO")


class MCPToolsCommandData:
    """Data structure for MCP tools commands."""

    def __init__(self, action: str, **kwargs):
        self.action = action  # list, call, info
        self.tool_name = kwargs.get("tool_name")
        self.args = kwargs.get("args")
        self.file = kwargs.get("file")
        self.format = kwargs.get("format", "json")
        self.type = kwargs.get("type")


class MCPValidateCommandData:
    """Data structure for MCP validate command."""

    def __init__(self, **kwargs):
        self.config = kwargs.get("config")
        self.format = kwargs.get("format", "table")


class InfrastructureCommandData:
    """Data structure for infrastructure commands."""

    def __init__(self, action: str, **kwargs):
        self.action = action  # discover, show, validate
        self.provider = kwargs.get("provider")
        self.all_providers = kwargs.get("all_providers", False)
        self.show = kwargs.get("show")
        self.all = kwargs.get("all", False)
        self.summary = kwargs.get("summary", False)


class ProviderOperationCommandData:
    """Data structure for provider operation commands."""

    def __init__(self, action: str, **kwargs):
        self.action = action  # select, exec
        self.provider = kwargs.get("provider")
        self.strategy = kwargs.get("strategy")
        self.operation = kwargs.get("operation")
        self.params = kwargs.get("params")


class TemplateUtilityCommandData:
    """Data structure for template utility commands."""

    def __init__(self, action: str, **kwargs):
        self.action = action  # refresh, generate
        self.force = kwargs.get("force", False)
        self.provider = kwargs.get("provider")
        self.all_providers = kwargs.get("all_providers", False)
        self.provider_api = kwargs.get("provider_api")
        self.provider_specific = kwargs.get("provider_specific", False)
        self.generic = kwargs.get("generic", False)
        self.provider_type = kwargs.get("provider_type")


class StorageTestCommandData:
    """Data structure for storage test command."""

    def __init__(self, **kwargs):
        self.strategy = kwargs.get("strategy")
        self.timeout = kwargs.get("timeout", 30)


class SystemServeCommandData:
    """Data structure for system serve command."""

    def __init__(self, **kwargs):
        self.host = kwargs.get("host", "0.0.0.0")
        self.port = kwargs.get("port", 8000)
        self.workers = kwargs.get("workers", 1)
        self.reload = kwargs.get("reload", False)
        self.server_log_level = kwargs.get("server_log_level", "info")


class CLICommandFactory:
    """Factory for converting CLI arguments to Commands/Queries."""

    # Template operations
    def create_list_templates_query(
        self,
        provider: Optional[str] = None,
        provider_api: Optional[str] = None,
        active_only: bool = True,
        filter_expressions: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ListTemplatesQuery:
        """Create query to list templates."""
        return ListTemplatesQuery(
            provider_name=provider,
            provider_api=provider_api,
            active_only=active_only,
            filter_expressions=filter_expressions or [],
        )

    def create_get_template_query(
        self,
        template_id: str,
        provider_name: Optional[str] = None,
        provider: Optional[str] = None,  # CLI compatibility
        **kwargs: Any,
    ) -> GetTemplateQuery:
        """Create query to get template details."""
        # Use provider if provider_name not specified (CLI compatibility)
        effective_provider = provider_name or provider
        return GetTemplateQuery(template_id=template_id, provider_name=effective_provider)

    def create_create_template_command(
        self,
        template_id: str,
        provider_api: str,
        image_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        instance_type: Optional[str] = None,
        subnet_ids: Optional[List[str]] = None,
        security_group_ids: Optional[List[str]] = None,
        tags: Optional[Dict[str, str]] = None,
        configuration: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> CreateTemplateCommand:
        """Create command to create template."""
        return CreateTemplateCommand(
            template_id=template_id,
            name=name,
            description=description,
            provider_api=provider_api,
            instance_type=instance_type,
            image_id=image_id,
            subnet_ids=subnet_ids or [],
            security_group_ids=security_group_ids or [],
            tags=tags or {},
            configuration=configuration or {},
        )

    def create_update_template_command(
        self,
        template_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        configuration: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> UpdateTemplateCommand:
        """Create command to update template."""
        return UpdateTemplateCommand(
            template_id=template_id,
            name=name,
            description=description,
            configuration=configuration or {},
        )

    def create_delete_template_command(
        self,
        template_id: str,
        **kwargs: Any,
    ) -> DeleteTemplateCommand:
        """Create command to delete template."""
        return DeleteTemplateCommand(template_id=template_id)

    def create_validate_template_command(
        self,
        template_id: str,
        configuration: Dict[str, Any],
        **kwargs: Any,
    ) -> ValidateTemplateCommand:
        """Create command to validate template."""
        return ValidateTemplateCommand(
            template_id=template_id,
            configuration=configuration,
        )

    # Request operations
    def create_create_request_command(
        self,
        template_id: str,
        count: int,
        request_id: Optional[str] = None,
        timeout: Optional[int] = None,
        tags: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> CreateRequestCommand:
        """Create command to request machines."""
        return CreateRequestCommand(
            request_id=request_id,
            template_id=template_id,
            requested_count=count,
            timeout=timeout,
            tags=tags,
            dry_run=dry_run,
        )

    def create_get_request_status_query(
        self,
        request_id: str,
        include_machines: bool = True,
        provider_name: Optional[str] = None,
        **kwargs: Any,
    ) -> GetRequestQuery:
        """Create query to get request status."""
        return GetRequestQuery(
            request_id=request_id,
            provider_name=provider_name,
            lightweight=False,  # CLI status should show full details including machines
        )

    def create_list_requests_query(
        self,
        status: Optional[str] = None,
        template_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        filter_expressions: Optional[List[str]] = None,
        provider_name: Optional[str] = None,
        provider: Optional[str] = None,  # CLI compatibility
        **kwargs: Any,
    ) -> ListRequestsQuery:
        """Create query to list requests."""
        # Use provider if provider_name not specified (CLI compatibility)
        effective_provider = provider_name or provider

        return ListRequestsQuery(
            status=status,
            template_id=template_id,
            limit=limit,
            offset=offset,
            filter_expressions=filter_expressions or [],
            provider_name=effective_provider,
        )

    def create_cancel_request_command(
        self,
        request_id: str,
        reason: str = "User requested cancellation",
        **kwargs: Any,
    ) -> CancelRequestCommand:
        """Create command to cancel request."""
        return CancelRequestCommand(
            request_id=request_id,
            reason=reason,
        )

    def create_return_request_command(
        self,
        machine_ids: List[str],
        timeout: Optional[int] = None,
        force_return: bool = False,
        **kwargs: Any,
    ) -> CreateReturnRequestCommand:
        """Create command to return machines."""
        return CreateReturnRequestCommand(
            machine_ids=machine_ids,
            timeout=timeout,
            force_return=force_return,
        )

    def create_list_return_requests_query(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        filter_expressions: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ListReturnRequestsQuery:
        """Create query to list return requests."""
        filters = {}
        if status:
            filters["status"] = status

        return ListReturnRequestsQuery(
            filters=filters,
            pagination={"limit": limit, "offset": offset},
            filter_expressions=filter_expressions or [],
        )

    # Machine operations
    def create_list_active_requests_query(
        self,
        provider_name: Optional[str] = None,
        filter_expressions: Optional[List[str]] = None,
        all_resources: bool = False,
        **kwargs: Any,
    ) -> ListActiveRequestsQuery:
        """Create query to list active requests."""
        return ListActiveRequestsQuery(
            provider_name=provider_name,
            filter_expressions=filter_expressions or [],
            all_resources=all_resources,
        )

    def create_list_machines_query(
        self,
        status: Optional[str] = None,
        template_id: Optional[str] = None,
        request_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        provider_name: Optional[str] = None,
        provider: Optional[str] = None,  # CLI compatibility
        all_resources: bool = False,
        **kwargs: Any,
    ) -> ListMachinesQuery:
        """Create query to list machines."""
        # Use provider if provider_name not specified (CLI compatibility)
        effective_provider = provider_name or provider

        return ListMachinesQuery(
            provider_name=effective_provider,
            template_id=template_id,
            status=status,
            request_id=request_id,
            filter_expressions=kwargs.get("filter_expressions", []),
            timestamp_format=kwargs.get("timestamp_format"),
            limit=limit,
            offset=offset,
            all_resources=all_resources,
        )

    def create_get_machine_query(
        self,
        machine_id: str,
        provider: Optional[str] = None,
        **kwargs: Any,
    ) -> GetMachineQuery:
        """Create query to get machine details."""
        return GetMachineQuery(machine_id=machine_id, provider_name=provider)

    def create_update_machine_status_command(
        self,
        machine_id: str,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> UpdateMachineStatusCommand:
        """Create command to update machine status."""
        return UpdateMachineStatusCommand(
            machine_id=machine_id,
            status=status,
            metadata=metadata or {},
        )

    # System operations
    def create_reload_provider_config_command(
        self,
        config_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ReloadProviderConfigCommand:
        """Create command to reload provider configuration."""
        return ReloadProviderConfigCommand(config_path=config_path)

    def create_refresh_templates_command(
        self,
        provider_name: Optional[str] = None,
        **kwargs: Any,
    ) -> RefreshTemplatesCommand:
        """Create command to refresh templates."""
        return RefreshTemplatesCommand(provider_name=provider_name)

    def create_get_system_status_query(
        self,
        include_provider_health: bool = True,
        detailed: bool = False,
        **kwargs: Any,
    ) -> GetSystemStatusQuery:
        """Create query to get system status."""
        return GetSystemStatusQuery(
            include_provider_health=include_provider_health,
            detailed=detailed,
        )

    def create_get_provider_config_query(
        self,
        provider_name: Optional[str] = None,
        include_sensitive: bool = False,
        **kwargs: Any,
    ) -> GetProviderConfigQuery:
        """Create query to get provider configuration."""
        return GetProviderConfigQuery(
            provider_name=provider_name,
            include_sensitive=include_sensitive,
        )

    def create_get_provider_metrics_query(
        self,
        provider_name: Optional[str] = None,
        timeframe: str = "1h",
        detailed: bool = False,
        **kwargs: Any,
    ) -> GetProviderMetricsQuery:
        """Create query to get provider metrics."""
        return GetProviderMetricsQuery(
            provider_name=provider_name,
            timeframe=timeframe,
            detailed=detailed,
        )

    def create_validate_provider_config_query(
        self,
        detailed: bool = True,
        **kwargs: Any,
    ) -> ValidateProviderConfigQuery:
        """Create query to validate provider configuration."""
        return ValidateProviderConfigQuery(detailed=detailed)

    # Storage operations
    def create_list_storage_strategies_query(
        self,
        include_current: bool = True,
        include_details: bool = False,
        filter_expressions: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ListStorageStrategiesQuery:
        """Create query to list storage strategies."""
        return ListStorageStrategiesQuery(
            include_current=include_current,
            include_details=include_details,
            filter_expressions=filter_expressions or [],
        )

    def create_get_storage_health_query(
        self,
        strategy_name: Optional[str] = None,
        detailed: bool = False,
        **kwargs: Any,
    ) -> GetStorageHealthQuery:
        """Create query to get storage health."""
        return GetStorageHealthQuery(
            strategy_name=strategy_name,
            detailed=detailed,
        )

    def create_get_storage_metrics_query(
        self,
        strategy_name: Optional[str] = None,
        time_range: str = "1h",
        include_operations: bool = True,
        **kwargs: Any,
    ) -> GetStorageMetricsQuery:
        """Create query to get storage metrics."""
        return GetStorageMetricsQuery(
            strategy_name=strategy_name,
            time_range=time_range,
            include_operations=include_operations,
        )

    # Scheduler operations
    def create_list_scheduler_strategies_query(
        self,
        include_current: bool = True,
        include_details: bool = False,
        filter_expressions: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ListSchedulerStrategiesQuery:
        """Create query to list scheduler strategies."""
        return ListSchedulerStrategiesQuery(
            include_current=include_current,
            include_details=include_details,
            filter_expressions=filter_expressions or [],
        )

    def create_get_scheduler_configuration_query(
        self,
        scheduler_name: Optional[str] = None,
        **kwargs: Any,
    ) -> GetSchedulerConfigurationQuery:
        """Create query to get scheduler configuration."""
        return GetSchedulerConfigurationQuery(scheduler_name=scheduler_name)

    def create_validate_scheduler_configuration_query(
        self,
        scheduler_name: Optional[str] = None,
        **kwargs: Any,
    ) -> ValidateSchedulerConfigurationQuery:
        """Create query to validate scheduler configuration."""
        return ValidateSchedulerConfigurationQuery(scheduler_name=scheduler_name)

    # Provider operations
    def create_get_provider_health_query(
        self,
        provider_name: Optional[str] = None,
        include_details: bool = True,
        include_history: bool = False,
        **kwargs: Any,
    ) -> GetProviderHealthQuery:
        """Create query to get provider health."""
        return GetProviderHealthQuery(
            provider_name=provider_name,
            include_details=include_details,
            include_history=include_history,
        )

    def create_list_available_providers_query(
        self,
        include_health: bool = True,
        include_capabilities: bool = True,
        include_metrics: bool = False,
        filter_healthy_only: bool = False,
        provider_type: Optional[str] = None,
        filter_expressions: Optional[List[str]] = None,
        provider_name: Optional[str] = None,
        **kwargs: Any,
    ) -> ListAvailableProvidersQuery:
        """Create query to list available providers."""
        return ListAvailableProvidersQuery(
            provider_name=provider_name,
            include_health=include_health,
            include_capabilities=include_capabilities,
            include_metrics=include_metrics,
            filter_healthy_only=filter_healthy_only,
            provider_type=provider_type,
            filter_expressions=filter_expressions or [],
        )

    def create_get_provider_capabilities_query(
        self,
        provider_name: str,
        include_performance_metrics: bool = True,
        include_limitations: bool = True,
        **kwargs: Any,
    ) -> GetProviderCapabilitiesQuery:
        """Create query to get provider capabilities."""
        return GetProviderCapabilitiesQuery(
            provider_name=provider_name,
            include_performance_metrics=include_performance_metrics,
            include_limitations=include_limitations,
        )

    def create_get_provider_strategy_config_query(
        self,
        include_selection_policies: bool = True,
        include_fallback_config: bool = True,
        include_health_check_config: bool = True,
        include_circuit_breaker_config: bool = True,
        **kwargs: Any,
    ) -> GetProviderStrategyConfigQuery:
        """Create query to get provider strategy configuration."""
        return GetProviderStrategyConfigQuery(
            include_selection_policies=include_selection_policies,
            include_fallback_config=include_fallback_config,
            include_health_check_config=include_health_check_config,
            include_circuit_breaker_config=include_circuit_breaker_config,
        )

    # Utility command data structures for non-CQRS commands
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

        # Handle positional vs flag arguments for request_ids
        request_ids = []
        if hasattr(args, "request_ids") and args.request_ids:
            request_ids.extend(args.request_ids)
        if hasattr(args, "flag_request_ids") and args.flag_request_ids:
            request_ids.extend(args.flag_request_ids)
        if hasattr(args, "request_id") and args.request_id:
            request_ids.append(args.request_id)
        if request_ids:
            args_dict["request_id"] = request_ids[0] if len(request_ids) == 1 else request_ids

        # Handle machine count (positional vs flag)
        if hasattr(args, "machine_count") and args.machine_count:
            args_dict["count"] = args.machine_count
        elif hasattr(args, "flag_machine_count") and args.flag_machine_count:
            args_dict["count"] = args.flag_machine_count

        # Map CLI resource/action to command/query
        resource = args.resource
        action = getattr(args, "action", None)

        # Normalize resource names (handle singular/plural)
        if resource in ["template", "machine", "request", "provider"]:
            resource = resource + "s"
        elif resource == "infra":
            resource = "infrastructure"

        # Use the existing mapping method
        return self.get_command_for_cli_args(resource, action, args_dict)

    def _process_input_data(self, args):
        """Process input data from -f/--file or -d/--data flags."""
        input_data = None
        if hasattr(args, "file") and args.file:
            try:
                import json

                with open(args.file) as f:
                    input_data = json.load(f)
            except Exception as e:
                from infrastructure.logging.logger import get_logger

                logger = get_logger(__name__)
                logger.error("Failed to load input file %s: %s", args.file, e)
                from domain.base.exceptions import DomainException

                raise DomainException(f"Failed to load input file: {e}")
        elif hasattr(args, "data") and args.data:
            try:
                import json

                input_data = json.loads(args.data)
            except Exception as e:
                from infrastructure.logging.logger import get_logger

                logger = get_logger(__name__)
                logger.error("Failed to parse input data: %s", e)
                from domain.base.exceptions import DomainException

                raise DomainException(f"Failed to parse input data: {e}")
        return input_data

    # Mapping methods for CLI command routing
    def get_command_for_cli_args(
        self,
        command_group: str,
        command_action: str,
        args: Dict[str, Any],
    ) -> Union[Any, None]:
        """
        Map CLI command group and action to appropriate Command/Query.

        Args:
            command_group: CLI command group (templates, requests, machines, system)
            command_action: CLI action (list, show, create, update, delete, status)
            args: CLI arguments dictionary

        Returns:
            Appropriate Command or Query instance, or None for non-CQRS commands
        """
        # Template operations
        if command_group == "templates":
            if command_action == "list":
                return ListTemplatesQuery(
                    provider_name=args.get("provider"),
                    provider_api=args.get("provider_api"),
                    active_only=args.get("active_only", True),
                    filter_expressions=args.get("filter") or [],
                )
            elif command_action == "show":
                template_id = args.get("template_id")
                if not template_id:
                    raise ValueError("template_id is required for show command")
                return self.create_get_template_query(
                    template_id=template_id, provider_name=args.get("provider")
                )
            elif command_action == "create":
                # Extract template data from input_data or args
                input_data = args.get("input_data") or {}
                return self.create_create_template_command(
                    template_id=input_data.get("templateId")
                    or input_data.get("template_id")
                    or args.get("template_id"),
                    provider_api=input_data.get("providerApi")
                    or input_data.get("provider_api")
                    or args.get("provider_api", "RunInstances"),
                    image_id=input_data.get("imageId")
                    or input_data.get("image_id")
                    or args.get("image_id"),
                    name=input_data.get("name") or args.get("name"),
                    description=input_data.get("description") or args.get("description"),
                    instance_type=input_data.get("instanceType")
                    or input_data.get("instance_type")
                    or args.get("instance_type"),
                    subnet_ids=input_data.get("subnetIds")
                    or input_data.get("subnet_ids")
                    or args.get("subnet_ids"),
                    security_group_ids=input_data.get("securityGroupIds")
                    or input_data.get("security_group_ids")
                    or args.get("security_group_ids"),
                    tags=input_data.get("tags") or args.get("tags"),
                    configuration=input_data or {},
                )
            elif command_action == "update":
                template_id = args.get("template_id")
                if not template_id:
                    raise ValueError("template_id is required for update command")
                input_data = args.get("input_data") or {}
                return self.create_update_template_command(
                    template_id=template_id,
                    name=input_data.get("name") or args.get("name"),
                    description=input_data.get("description") or args.get("description"),
                    configuration=input_data or {},
                )
            elif command_action == "delete":
                template_id = args.get("template_id")
                if not template_id:
                    raise ValueError("template_id is required for delete command")
                return self.create_delete_template_command(template_id=template_id)
            elif command_action == "validate":
                input_data = args.get("input_data") or {}
                if not input_data:
                    raise ValueError("Template configuration is required for validate command")
                return self.create_validate_template_command(
                    template_id=input_data.get("template_id", "validation"),
                    configuration=input_data,
                )
            elif command_action == "refresh":
                return self.create_refresh_templates_command(provider_name=args.get("provider"))
            elif command_action == "generate":
                generate_args = {k: v for k, v in args.items() if k != "action"}
                return self.create_template_utility_command_data("generate", **generate_args)

        # Request operations
        elif command_group == "requests":
            if command_action == "create":
                input_data = args.get("input_data") or {}
                template_id = input_data.get("template_id") or args.get("template_id")
                count = input_data.get("count") or args.get("count", 1)
                if not template_id:
                    raise ValueError("template_id is required for create request")
                return self.create_create_request_command(
                    template_id=template_id,
                    count=count,
                    request_id=args.get("request_id"),
                    timeout=args.get("timeout"),
                    tags=input_data.get("tags") or args.get("tags"),
                    dry_run=args.get("dry_run", False),
                )
            elif command_action == "show":
                # Show command: single entity via CQRS
                request_id = args.get("request_id")
                if not request_id:
                    raise ValueError("request_id is required for show command")

                return self.create_get_request_status_query(
                    request_id=request_id, provider_name=args.get("provider"), include_machines=True
                )
            elif command_action == "status":
                # Status command: multiple entities via interface handler
                return None
            elif command_action == "list":
                return self.create_list_requests_query(
                    provider_name=args.get("provider"),
                    status=args.get("status"),
                    template_id=args.get("template_id"),
                    limit=args.get("limit") or 50,
                    offset=args.get("offset") or 0,
                    filter_expressions=args.get("filter") or [],
                )
            elif command_action == "cancel":
                request_id = args.get("request_id")
                if not request_id:
                    raise ValueError("request_id is required for cancel command")
                return self.create_cancel_request_command(
                    request_id=request_id, reason=args.get("reason", "User requested cancellation")
                )

        # Machine operations
        elif command_group == "machines":
            if command_action == "list":
                return self.create_list_machines_query(
                    provider_name=args.get("provider"),
                    status=args.get("status"),
                    template_id=args.get("template_id"),
                    request_id=args.get("request_id"),
                    limit=args.get("limit") or 50,
                    offset=args.get("offset") or 0,
                    filter_expressions=args.get("filter") or [],
                    timestamp_format=args.get("timestamp_format"),
                    all_resources=args.get("all", False),
                )
            elif command_action == "request":
                # Alias for requests create
                input_data = args.get("input_data") or {}
                template_id = input_data.get("template_id") or args.get("template_id")
                count = input_data.get("count") or args.get("count", 1)
                if not template_id:
                    raise ValueError("template_id is required for machine request")
                return self.create_create_request_command(
                    template_id=template_id,
                    count=count,
                    timeout=args.get("timeout"),
                    tags=input_data.get("tags") or args.get("tags"),
                    dry_run=args.get("dry_run", False),
                )
            elif command_action == "return":
                # Check for --all flag first
                if args.get("all", False):
                    return None  # Route to interface handler for --all support

                machine_ids = args.get("machine_ids", [])
                if not machine_ids:
                    raise ValueError("machine_ids are required for return command")
                return self.create_return_request_command(
                    machine_ids=machine_ids,
                    timeout=args.get("timeout"),
                    force_return=args.get("force", False),
                )
            elif command_action == "show":
                # Show command: single entity via CQRS
                machine_id = args.get("machine_id")
                if not machine_id:
                    raise ValueError("machine_id is required for show command")
                return self.create_get_machine_query(machine_id=machine_id)
            elif command_action == "status":
                # Status command: multiple entities via interface handler
                return None

        # System operations
        elif command_group == "system":
            if command_action == "status":
                return self.create_get_system_status_query(
                    include_provider_health=True, detailed=args.get("detailed", False)
                )
            elif command_action == "health":
                return self.create_get_system_status_query(
                    include_provider_health=True, detailed=args.get("detailed", False)
                )
            elif command_action == "metrics":
                return self.create_get_provider_metrics_query(
                    provider_name=args.get("provider"), detailed=args.get("detailed", False)
                )
            elif command_action == "serve":
                return self.create_system_serve_command_data(**args)
            elif command_action == "reload-config":
                return self.create_reload_provider_config_command(config_path=args.get("config"))

        # Storage operations
        elif command_group == "storage":
            if command_action == "list":
                return self.create_list_storage_strategies_query(
                    include_current=True,
                    include_details=args.get("detailed", False),
                    filter_expressions=args.get("filter") or [],
                )
            elif command_action == "show":
                return self.create_get_storage_health_query(
                    strategy_name=args.get("strategy"), detailed=True
                )
            elif command_action == "health":
                return self.create_get_storage_health_query(
                    strategy_name=args.get("strategy"), detailed=args.get("detailed", False)
                )
            elif command_action == "test":
                return self.create_test_storage_command()
            elif command_action == "metrics":
                return self.create_get_storage_metrics_query(
                    strategy_name=args.get("strategy"), time_range=args.get("time_range", "1h")
                )
            elif command_action == "validate":
                return self.create_get_storage_health_query(
                    strategy_name=args.get("strategy"), detailed=True
                )

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

        # Provider operations
        elif command_group == "providers":
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

        # Infrastructure operations
        elif command_group == "infrastructure" or command_group == "infra":
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

    def create_execute_provider_operation_command(
        self,
        operation: str,
        params: Optional[str] = None,
        provider_name: Optional[str] = None,
        **kwargs: Any,
    ) -> ExecuteProviderOperationCommand:
        """Create command to execute provider operation."""
        from providers.base.strategy import ProviderOperation, ProviderOperationType
        import json

        # Parse params if provided
        parsed_params = {}
        if params:
            try:
                parsed_params = json.loads(params)
            except json.JSONDecodeError:
                raise ValueError(f"Invalid JSON in params: {params}")

        # Create ProviderOperation
        provider_operation = ProviderOperation(
            operation_type=ProviderOperationType(operation),
            parameters=parsed_params,
            context={"provider_override": provider_name} if provider_name else {},
        )

        return ExecuteProviderOperationCommand(
            operation=provider_operation, strategy_override=provider_name
        )

    def create_test_storage_command(self) -> TestStorageCommand:
        """Create command to test storage."""
        return TestStorageCommand()

    def create_mcp_validate_command(self) -> MCPValidateCommand:
        """Create command to validate MCP."""
        return MCPValidateCommand()

    def create_get_configuration_query(
        self,
        key: str,
        default: Optional[str] = None,
        **kwargs: Any,
    ) -> GetConfigurationQuery:
        """Create query to get configuration value."""
        return GetConfigurationQuery(key=key, default=default)

    def create_set_configuration_command(
        self,
        key: str,
        value: str,
        **kwargs: Any,
    ) -> SetConfigurationCommand:
        """Create command to set configuration value."""
        return SetConfigurationCommand(key=key, value=value)

    # Bulk query factory methods
    def create_get_multiple_requests_query(
        self,
        request_ids: List[str],
        provider_name: Optional[str] = None,
        lightweight: bool = False,
        include_machines: bool = True,
        **kwargs: Any,
    ) -> "GetMultipleRequestsQuery":
        """Create query to get multiple requests by IDs."""
        from application.dto.bulk_queries import GetMultipleRequestsQuery

        return GetMultipleRequestsQuery(
            request_ids=request_ids,
            provider_name=provider_name,
            lightweight=lightweight,
            include_machines=include_machines,
        )

    def create_get_multiple_templates_query(
        self,
        template_ids: List[str],
        provider_name: Optional[str] = None,
        active_only: bool = True,
        **kwargs: Any,
    ) -> "GetMultipleTemplatesQuery":
        """Create query to get multiple templates by IDs."""
        from application.dto.bulk_queries import GetMultipleTemplatesQuery

        return GetMultipleTemplatesQuery(
            template_ids=template_ids, provider_name=provider_name, active_only=active_only
        )

    def create_get_multiple_machines_query(
        self,
        machine_ids: List[str],
        provider_name: Optional[str] = None,
        include_requests: bool = True,
        **kwargs: Any,
    ) -> "GetMultipleMachinesQuery":
        """Create query to get multiple machines by IDs."""
        from application.dto.bulk_queries import GetMultipleMachinesQuery

        return GetMultipleMachinesQuery(
            machine_ids=machine_ids, provider_name=provider_name, include_requests=include_requests
        )


# Global factory instance
cli_command_factory = CLICommandFactory()
