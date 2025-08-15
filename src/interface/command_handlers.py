"""Command handlers orchestrator for the interface layer.

This module provides a integrated interface to all command handlers organized by responsibility:
- Template operations (handle_list_templates, handle_get_template, etc.)
- Request operations (handle_get_request_status, handle_request_machines, etc.)
- Storage operations (handle_list_storage_strategies, etc.)
- Scheduler operations (handle_list_scheduler_strategies, etc.)
- System operations (handle_provider_health, etc.)
"""

# Import base handler
from application.base.command_handler import CLICommandHandler
from interface.request_command_handlers import (
    handle_get_request_status,
    handle_get_return_requests,
    handle_request_machines,
    handle_request_return_machines,
)
from interface.scheduler_command_handlers import (
    handle_list_scheduler_strategies,
    handle_show_scheduler_config,
    handle_validate_scheduler_config,
)
from interface.storage_command_handlers import (
    handle_list_storage_strategies,
    handle_show_storage_config,
    handle_storage_health,
    handle_storage_metrics,
    handle_test_storage,
    handle_validate_storage_config,
)
from interface.system_command_handlers import (
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
from interface.template_command_handlers import (
    handle_get_template,
    handle_list_templates,
    handle_validate_template,
)

__all__ = [
    # Base handler
    "CLICommandHandler",
    # Template handlers (function-based)
    "handle_list_templates",
    "handle_get_template",
    "handle_validate_template",
    # Request handlers (function-based)
    "handle_get_request_status",
    "handle_request_machines",
    "handle_get_return_requests",
    "handle_request_return_machines",
    # System handlers (function-based)
    "handle_provider_health",
    "handle_list_providers",
    "handle_provider_config",
    "handle_validate_provider_config",
    "handle_reload_provider_config",
    "handle_select_provider_strategy",
    "handle_execute_provider_operation",
    "handle_provider_metrics",
    # Storage handlers (function-based)
    "handle_list_storage_strategies",
    "handle_show_storage_config",
    "handle_validate_storage_config",
    "handle_test_storage",
    "handle_storage_health",
    "handle_storage_metrics",
    # Scheduler handlers (function-based)
    "handle_list_scheduler_strategies",
    "handle_show_scheduler_config",
    "handle_validate_scheduler_config",
]
