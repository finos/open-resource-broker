"""Command handlers orchestrator for the interface layer.

This module provides a integrated interface to all command handlers organized by responsibility:
- Template operations (TemplateReadHandlers, TemplateWriteHandlers)
- Request operations (RequestReadHandlers, RequestWriteHandlers)
- Machine operations (MachineHandlers)
- Config, storage and scheduler operations (ConfigHandlers, StorageHandlers,
  SchedulerHandlers)
- System operations (handle_provider_health, etc.)
"""

# Import base handler
from orb.application.base.command_handler import CLICommandHandler
from orb.interface.config_command_handlers import ConfigHandlers
from orb.interface.machine_command_handlers import MachineHandlers
from orb.interface.request_command_handlers import RequestWriteHandlers
from orb.interface.request_query_handlers import RequestReadHandlers
from orb.interface.scheduler_command_handlers import SchedulerHandlers
from orb.interface.storage_command_handlers import StorageHandlers
from orb.interface.system_command_handlers import (
    handle_execute_provider_operation,
    handle_list_providers,
    handle_provider_config,
    handle_provider_health,
    handle_provider_metrics,
    handle_reload_provider_config,
    handle_select_provider_strategy,
)

# Import specialized handlers by category
from orb.interface.template_command_handlers import (
    TemplateReadHandlers,
    TemplateWriteHandlers,
)

__all__: list[str] = [
    # Base handler
    "CLICommandHandler",
    "handle_execute_provider_operation",
    # Machine handlers (class-based)
    "MachineHandlers",
    # Request handlers (class-based)
    "RequestWriteHandlers",
    "RequestReadHandlers",
    "handle_list_providers",
    # Config handlers (class-based)
    "ConfigHandlers",
    # Scheduler handlers (class-based)
    "SchedulerHandlers",
    # Storage handlers (class-based)
    "StorageHandlers",
    # Template handlers (class-based)
    "TemplateWriteHandlers",
    "TemplateReadHandlers",
    "handle_provider_config",
    # System handlers (function-based)
    "handle_provider_health",
    "handle_provider_metrics",
    "handle_reload_provider_config",
    "handle_select_provider_strategy",
]
