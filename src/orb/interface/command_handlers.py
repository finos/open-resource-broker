"""Command handlers orchestrator for the interface layer.

This module provides a integrated interface to all command handlers organized by responsibility:
- Template operations (handle_list_templates, handle_get_template, etc.)
- Request operations (handle_get_request_status, handle_request_machines, etc.)
- Storage operations (handle_list_storage_strategies, etc.)
- Scheduler operations (handle_list_scheduler_strategies, etc.)
- System operations (handle_provider_health, etc.)
"""

# Import base handler
from orb.application.base.command_handler import CLICommandHandler
from orb.interface.machine_command_handlers import (
    handle_get_machine_status,
)
from orb.interface.request_command_handlers import (
    handle_get_request_status,
    handle_get_return_requests,
    handle_request_machines,
    handle_request_return_machines,
)
from orb.interface.scheduler_command_handlers import (
    handle_list_scheduler_strategies,
    handle_show_scheduler_config,
    handle_validate_scheduler_config,
)
from orb.interface.storage_command_handlers import (
    handle_list_storage_strategies,
    handle_show_storage_config,
    handle_storage_health,
    handle_storage_metrics,
    handle_test_storage,
    handle_validate_storage_config,
)
from orb.interface.system_command_handlers import (
    handle_execute_provider_operation,
    handle_list_providers,
    handle_provider_config,
    handle_provider_health,
    handle_provider_metrics,
    handle_reload_provider_config,
    handle_select_provider_strategy,
    handle_validate_provider_config,
)

# Import specialized handlers by category
from orb.interface.template_command_handlers import (
    handle_get_template,
    handle_list_templates,
    handle_validate_template,
)

__all__: list[str] = [
    # Base handler
    "CLICommandHandler",
    "handle_execute_provider_operation",
    # Request handlers (function-based)
    "handle_get_request_status",
    "handle_get_return_requests",
    "handle_get_machine_status",
    "handle_get_template",
    "handle_list_providers",
    # Scheduler handlers (function-based)
    "handle_list_scheduler_strategies",
    # Storage handlers (function-based)
    "handle_list_storage_strategies",
    # Template handlers (function-based)
    "handle_list_templates",
    "handle_provider_config",
    # System handlers (function-based)
    "handle_provider_health",
    "handle_provider_metrics",
    "handle_reload_provider_config",
    "handle_request_machines",
    "handle_request_return_machines",
    "handle_select_provider_strategy",
    "handle_show_scheduler_config",
    "handle_show_storage_config",
    "handle_storage_health",
    "handle_storage_metrics",
    "handle_test_storage",
    "handle_validate_provider_config",
    "handle_validate_scheduler_config",
    "handle_validate_storage_config",
    "handle_validate_template",
]
