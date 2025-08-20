"""
Main CLI module with argument parsing and command execution.

This module provides the main CLI interface including:
- Command line argument parsing
- Command routing and execution
- Integration with application services
"""

import argparse
import asyncio
import logging
import os
import sys
from typing import Any, Dict

from _package import REPO_URL
from cli.completion import generate_bash_completion, generate_zsh_completion
from cli.formatters import format_output
from domain.base.exceptions import DomainException
from domain.request.value_objects import RequestStatus
from infrastructure.logging.logger import get_logger


def parse_args() -> tuple[argparse.Namespace, dict]:
    """Parse command line arguments with resource-action structure.

    Returns:
        tuple: (parsed_args, resource_parsers_dict)
    """

    # Main parser with global options
    parser = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description="Open HostFactory Plugin - Cloud resource management for IBM Spectrum Symphony",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  %(prog)s templates list                    # List all templates
  %(prog)s templates list --legacy           # List in legacy format
  %(prog)s templates list --format table     # Display as table
  %(prog)s machines request template-id 5    # Request 5 machines
  %(prog)s requests list --status pending    # List pending requests

For more information, visit: {REPO_URL}
        """,
    )

    # Global options
    parser.add_argument("--config", help="Configuration file path")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set logging level",
    )
    parser.add_argument(
        "--format",
        choices=["json", "yaml", "table", "list"],
        default="json",
        help="Output format",
    )
    parser.add_argument("--output", help="Output file (default: stdout)")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-essential output")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without executing",
    )
    parser.add_argument(
        "--scheduler",
        choices=["default", "hostfactory", "hf"],
        help="Override scheduler strategy for this command",
    )
    parser.add_argument(
        "--completion", choices=["bash", "zsh"], help="Generate shell completion script"
    )

    # HostFactory compatibility flags
    parser.add_argument("-f", "--file", help="Input JSON file path (HostFactory compatibility)")
    parser.add_argument("-d", "--data", help="Input JSON data string (HostFactory compatibility)")
    # Get version dynamically
    try:
        from _package import __version__

        version_string = f"%(prog)s {__version__}"
    except ImportError:
        version_string = "%(prog)s develop"  # Fallback

    parser.add_argument("--version", action="version", version=version_string)

    # Resource subparsers - but also allow legacy commands as first argument
    subparsers = parser.add_subparsers(
        dest="resource", help="Available resources or legacy commands"
    )

    # Store resource parser references for systematic help display
    resource_parsers = {}

    # Add legacy command support by making resource more flexible
    # This allows both 'templates list' and 'getAvailableTemplates' to work

    # Templates resource
    templates_parser = subparsers.add_parser("templates", help="Manage compute templates")
    resource_parsers["templates"] = templates_parser
    templates_subparsers = templates_parser.add_subparsers(
        dest="action", help="Template actions", required=True
    )

    # Templates list
    templates_list = templates_subparsers.add_parser("list", help="List all templates")
    templates_list.add_argument("--provider-api", help="Filter by provider API type")
    templates_list.add_argument(
        "--long", action="store_true", help="Include detailed configuration fields"
    )
    templates_list.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )

    # Templates show
    templates_show = templates_subparsers.add_parser("show", help="Show template details")
    templates_show.add_argument("template_id", help="Template ID to show")
    templates_show.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )

    # Templates create
    templates_create = templates_subparsers.add_parser("create", help="Create new template")
    templates_create.add_argument("--file", required=True, help="Template configuration file")
    templates_create.add_argument(
        "--validate-only", action="store_true", help="Only validate, do not create"
    )

    # Templates update
    templates_update = templates_subparsers.add_parser("update", help="Update existing template")
    templates_update.add_argument("template_id", help="Template ID to update")
    templates_update.add_argument(
        "--file", required=True, help="Updated template configuration file"
    )

    # Templates delete
    templates_delete = templates_subparsers.add_parser("delete", help="Delete template")
    templates_delete.add_argument("template_id", help="Template ID to delete")
    templates_delete.add_argument(
        "--force", action="store_true", help="Force deletion without confirmation"
    )

    # Templates validate
    templates_validate = templates_subparsers.add_parser("validate", help="Validate template")
    templates_validate.add_argument("--file", required=True, help="Template file to validate")

    # Templates refresh
    templates_refresh = templates_subparsers.add_parser("refresh", help="Refresh template cache")
    templates_refresh.add_argument("--force", action="store_true", help="Force complete refresh")

    # Machines resource
    machines_parser = subparsers.add_parser("machines", help="Manage compute instances")
    resource_parsers["machines"] = machines_parser
    machines_subparsers = machines_parser.add_subparsers(
        dest="action", help="Machine actions", required=True
    )

    # Machines list
    machines_list = machines_subparsers.add_parser("list", help="List all machines")
    machines_list.add_argument("--status", help="Filter by machine status")
    machines_list.add_argument("--template-id", help="Filter by template ID")
    machines_list.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )

    # Machines show
    machines_show = machines_subparsers.add_parser("show", help="Show machine details")
    machines_show.add_argument("machine_id", help="Machine ID to show")
    machines_show.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )

    # Machines request (create machines)
    machines_request = machines_subparsers.add_parser("request", help="Request new machines")
    machines_request.add_argument(
        "template_id",
        nargs="?",
        help="Template ID to use (optional if using -f/--file or -d/--data)",
    )
    machines_request.add_argument(
        "machine_count",
        nargs="?",
        type=int,
        help="Number of machines to request (optional if using -f/--file or -d/--data)",
    )
    machines_request.add_argument(
        "--wait", action="store_true", help="Wait for machines to be ready"
    )
    machines_request.add_argument(
        "--timeout", type=int, default=300, help="Wait timeout in seconds"
    )

    # Machines return (terminate machines)
    machines_return = machines_subparsers.add_parser("return", help="Return machines")
    machines_return.add_argument("machine_ids", nargs="+", help="Machine IDs to return")
    machines_return.add_argument(
        "--force", action="store_true", help="Force return without confirmation"
    )

    # Machines status
    machines_status = machines_subparsers.add_parser("status", help="Check machine status")
    machines_status.add_argument("machine_ids", nargs="+", help="Machine IDs to check")

    # Requests resource
    requests_parser = subparsers.add_parser("requests", help="Manage provisioning requests")
    resource_parsers["requests"] = requests_parser
    requests_subparsers = requests_parser.add_subparsers(
        dest="action", help="Request actions", required=True
    )

    # Requests list
    requests_list = requests_subparsers.add_parser("list", help="List all requests")
    requests_list.add_argument(
        "--status",
        choices=[s.value for s in RequestStatus],
        help="Filter by request status",
    )
    requests_list.add_argument("--template-id", help="Filter by template ID")
    requests_list.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )

    # Requests show
    requests_show = requests_subparsers.add_parser("show", help="Show request details")
    requests_show.add_argument("request_id", help="Request ID to show")
    requests_show.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )

    # Requests cancel
    requests_cancel = requests_subparsers.add_parser("cancel", help="Cancel request")
    requests_cancel.add_argument("request_id", help="Request ID to cancel")
    requests_cancel.add_argument("--force", action="store_true", help="Force cancellation")

    # Requests status
    requests_status = requests_subparsers.add_parser("status", help="Check request status")
    requests_status.add_argument("request_ids", nargs="+", help="Request IDs to check")

    # System resource
    system_parser = subparsers.add_parser("system", help="System operations")
    resource_parsers["system"] = system_parser
    system_subparsers = system_parser.add_subparsers(
        dest="action", help="System actions", required=True
    )

    # System status
    system_status = system_subparsers.add_parser("status", help="Show system status")
    system_status.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )

    # System health
    system_health = system_subparsers.add_parser("health", help="Run health check")
    system_health.add_argument(
        "--detailed", action="store_true", help="Show detailed health information"
    )

    # System metrics
    system_metrics = system_subparsers.add_parser("metrics", help="Show system metrics")
    system_metrics.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )

    # System serve
    system_serve = system_subparsers.add_parser("serve", help="Start REST API server")
    system_serve.add_argument("--host", default="0.0.0.0", help="Server host")  # nosec B104
    system_serve.add_argument("--port", type=int, default=8000, help="Server port")
    system_serve.add_argument("--workers", type=int, default=1, help="Number of workers")
    system_serve.add_argument("--reload", action="store_true", help="Enable auto-reload")
    system_serve.add_argument("--server-log-level", default="info", help="Server log level")

    # Config resource
    config_parser = subparsers.add_parser("config", help="Configuration management")
    resource_parsers["config"] = config_parser
    config_subparsers = config_parser.add_subparsers(
        dest="action", help="Config actions", required=True
    )

    # Config show
    config_show = config_subparsers.add_parser("show", help="Show configuration")
    config_show.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )

    # Config set
    config_set = config_subparsers.add_parser("set", help="Set configuration value")
    config_set.add_argument("key", help="Configuration key")
    config_set.add_argument("value", help="Configuration value")

    # Config get
    config_get = config_subparsers.add_parser("get", help="Get configuration value")
    config_get.add_argument("key", help="Configuration key")

    # Config validate
    config_validate = config_subparsers.add_parser("validate", help="Validate configuration")
    config_validate.add_argument("--file", help="Configuration file to validate")

    # Providers resource
    providers_parser = subparsers.add_parser("providers", help="Provider management")
    resource_parsers["providers"] = providers_parser
    providers_subparsers = providers_parser.add_subparsers(
        dest="action", help="Provider actions", required=True
    )

    # Providers list
    providers_list = providers_subparsers.add_parser("list", help="List available providers")
    providers_list.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    providers_list.add_argument(
        "--detailed", action="store_true", help="Show detailed provider information"
    )

    # Providers show
    providers_show = providers_subparsers.add_parser("show", help="Show provider details")
    providers_show.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    providers_show.add_argument("--provider", help="Show specific provider details")

    # Providers health
    providers_health = providers_subparsers.add_parser("health", help="Check provider health")
    providers_health.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    providers_health.add_argument("--provider", help="Check specific provider health")

    # Providers select
    providers_select = providers_subparsers.add_parser("select", help="Select provider strategy")
    providers_select.add_argument("provider", help="Provider name to select")
    providers_select.add_argument("--strategy", help="Specific strategy to select")

    # Providers exec
    providers_exec = providers_subparsers.add_parser("exec", help="Execute provider operation")
    providers_exec.add_argument("operation", help="Operation to execute")
    providers_exec.add_argument("--provider", help="Provider to execute operation on")
    providers_exec.add_argument("--params", help="Operation parameters (JSON format)")

    # Providers metrics
    providers_metrics = providers_subparsers.add_parser("metrics", help="Show provider metrics")
    providers_metrics.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    providers_metrics.add_argument("--provider", help="Show metrics for specific provider")

    # Storage resource
    storage_parser = subparsers.add_parser("storage", help="Storage management")
    resource_parsers["storage"] = storage_parser
    storage_subparsers = storage_parser.add_subparsers(
        dest="action", help="Storage actions", required=True
    )

    # Storage list
    storage_list = storage_subparsers.add_parser("list", help="List available storage strategies")
    storage_list.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )

    # Storage show
    storage_show = storage_subparsers.add_parser("show", help="Show current storage configuration")
    storage_show.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    storage_show.add_argument("--strategy", help="Show specific storage strategy details")

    # Storage validate
    storage_validate = storage_subparsers.add_parser(
        "validate", help="Validate storage configuration"
    )
    storage_validate.add_argument("--strategy", help="Validate specific storage strategy")

    # Storage test
    storage_test = storage_subparsers.add_parser("test", help="Test storage connectivity")
    storage_test.add_argument("--strategy", help="Test specific storage strategy")
    storage_test.add_argument("--timeout", type=int, default=30, help="Test timeout in seconds")

    # Storage health
    storage_health = storage_subparsers.add_parser("health", help="Check storage health")
    storage_health.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    storage_health.add_argument(
        "--detailed", action="store_true", help="Show detailed health information"
    )

    # Storage metrics
    storage_metrics = storage_subparsers.add_parser(
        "metrics", help="Show storage performance metrics"
    )
    storage_metrics.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    storage_metrics.add_argument("--strategy", help="Show metrics for specific storage strategy")

    # Scheduler resource
    scheduler_parser = subparsers.add_parser("scheduler", help="Scheduler management")
    resource_parsers["scheduler"] = scheduler_parser
    scheduler_subparsers = scheduler_parser.add_subparsers(
        dest="action", help="Scheduler actions", required=True
    )

    # Scheduler list
    scheduler_list = scheduler_subparsers.add_parser(
        "list", help="List available scheduler strategies"
    )
    scheduler_list.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    scheduler_list.add_argument("--long", action="store_true", help="Show detailed information")

    # Scheduler show
    scheduler_show = scheduler_subparsers.add_parser("show", help="Show scheduler configuration")
    scheduler_show.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    scheduler_show.add_argument("--scheduler", help="Show specific scheduler strategy details")

    # Scheduler validate
    scheduler_validate = scheduler_subparsers.add_parser(
        "validate", help="Validate scheduler configuration"
    )
    scheduler_validate.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], help="Output format"
    )
    scheduler_validate.add_argument("--scheduler", help="Validate specific scheduler strategy")

    # MCP resource
    mcp_parser = subparsers.add_parser("mcp", help="MCP (Model Context Protocol) operations")
    resource_parsers["mcp"] = mcp_parser
    mcp_subparsers = mcp_parser.add_subparsers(dest="action", help="MCP actions", required=True)

    # MCP tools
    mcp_tools = mcp_subparsers.add_parser("tools", help="MCP tools management")
    mcp_tools_sub = mcp_tools.add_subparsers(dest="tools_action", required=True)

    # MCP tools list
    mcp_tools_list = mcp_tools_sub.add_parser("list", help="List available MCP tools")
    mcp_tools_list.add_argument(
        "--format",
        choices=["json", "yaml", "table"],
        default="table",
        help="Output format",
    )
    mcp_tools_list.add_argument(
        "--type", choices=["command", "query"], help="Filter tools by handler type"
    )

    # MCP tools call
    mcp_tools_call = mcp_tools_sub.add_parser("call", help="Call MCP tool directly")
    mcp_tools_call.add_argument("tool_name", help="Name of tool to call")
    mcp_tools_call.add_argument("--args", help="Tool arguments as JSON string")
    mcp_tools_call.add_argument("--file", help="Tool arguments from JSON file")
    mcp_tools_call.add_argument(
        "--format",
        choices=["json", "yaml", "table"],
        default="json",
        help="Output format",
    )

    # MCP tools info
    mcp_tools_info = mcp_tools_sub.add_parser("info", help="Get information about MCP tool")
    mcp_tools_info.add_argument("tool_name", help="Name of tool to get info for")
    mcp_tools_info.add_argument(
        "--format",
        choices=["json", "yaml", "table"],
        default="table",
        help="Output format",
    )

    # MCP validate
    mcp_validate = mcp_subparsers.add_parser("validate", help="Validate MCP configuration")
    mcp_validate.add_argument("--config", help="MCP configuration file to validate")
    mcp_validate.add_argument(
        "--format",
        choices=["json", "yaml", "table"],
        default="table",
        help="Output format",
    )

    # MCP serve
    mcp_serve = mcp_subparsers.add_parser("serve", help="Start MCP server")
    mcp_serve.add_argument("--port", type=int, default=3000, help="Server port (default: 3000)")
    mcp_serve.add_argument("--host", default="localhost", help="Server host (default: localhost)")
    mcp_serve.add_argument(
        "--stdio",
        action="store_true",
        help="Run in stdio mode for direct MCP client communication",
    )
    mcp_serve.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level for MCP server",
    )

    return parser.parse_args(), resource_parsers


async def execute_command(args, app) -> Dict[str, Any]:
    """Execute the appropriate command handler."""
    # Process input data from -f/--file or -d/--data flags (HostFactory compatibility)
    input_data = None
    if hasattr(args, "file") and args.file:
        try:
            import json

            with open(args.file, "r") as f:
                input_data = json.load(f)
        except Exception as e:
            from infrastructure.logging.logger import get_logger

            logger = get_logger(__name__)
            logger.error("Failed to load input file %s: %s", args.file, e)
            raise DomainException(f"Failed to load input file: {e}")
    elif hasattr(args, "data") and args.data:
        try:
            import json

            input_data = json.loads(args.data)
        except Exception as e:
            from infrastructure.logging.logger import get_logger

            logger = get_logger(__name__)
            logger.error("Failed to parse input data: %s", e)
            raise DomainException(f"Failed to parse input data: {e}")

    # Add input_data to args for handlers to use
    args.input_data = input_data

    # Handle nested MCP commands
    if args.resource == "mcp" and args.action == "tools":
        handler_key = ("mcp", "tools", getattr(args, "tools_action", None))
    else:
        handler_key = (args.resource, args.action)

    # Handle global scheduler override
    scheduler_override_active = False
    if hasattr(args, "scheduler") and args.scheduler:
        try:
            app.config_manager.override_scheduler_strategy(args.scheduler)
            scheduler_override_active = True
        except Exception as e:
            from infrastructure.logging.logger import get_logger

            logger = get_logger(__name__)
            logger.warning("Failed to override scheduler strategy: %s", e)

    try:
        # Import function handlers - all are now async functions with decorators
        from interface.mcp.server.handler import handle_mcp_serve
        from interface.mcp_command_handlers import (
            handle_mcp_tools_call,
            handle_mcp_tools_info,
            handle_mcp_tools_list,
            handle_mcp_validate,
        )
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
        from interface.serve_command_handler import handle_serve_api
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
        from interface.template_command_handlers import (
            handle_create_template,
            handle_delete_template,
            handle_get_template,
            handle_list_templates,
            handle_refresh_templates,
            handle_update_template,
            handle_validate_template,
        )

        # Command handler mapping - all handlers are now async functions
        COMMAND_HANDLERS = {
            # Templates - Complete CRUD operations
            ("templates", "list"): handle_list_templates,
            ("templates", "show"): handle_get_template,
            ("templates", "create"): handle_create_template,
            ("templates", "update"): handle_update_template,
            ("templates", "delete"): handle_delete_template,
            ("templates", "validate"): handle_validate_template,
            ("templates", "refresh"): handle_refresh_templates,
            # Machines
            ("machines", "request"): handle_request_machines,
            ("machines", "return"): handle_request_return_machines,
            ("machines", "list"): handle_request_machines,
            ("machines", "show"): handle_request_machines,
            # Requests
            ("requests", "status"): handle_get_request_status,
            ("requests", "list"): handle_get_return_requests,
            ("requests", "show"): handle_get_request_status,
            ("requests", "cancel"): handle_get_request_status,
            ("requests", "retry"): handle_get_request_status,
            # Providers
            ("providers", "health"): handle_provider_health,
            ("providers", "list"): handle_list_providers,
            ("providers", "show"): handle_list_providers,
            ("providers", "select"): handle_select_provider_strategy,
            ("providers", "exec"): handle_execute_provider_operation,
            ("providers", "metrics"): handle_provider_metrics,
            # Storage commands
            ("storage", "list"): handle_list_storage_strategies,
            ("storage", "show"): handle_show_storage_config,
            ("storage", "validate"): handle_validate_storage_config,
            ("storage", "test"): handle_test_storage,
            ("storage", "health"): handle_storage_health,
            ("storage", "metrics"): handle_storage_metrics,
            # Scheduler commands
            ("scheduler", "list"): handle_list_scheduler_strategies,
            ("scheduler", "show"): handle_show_scheduler_config,
            ("scheduler", "validate"): handle_validate_scheduler_config,
            # System commands
            ("system", "serve"): handle_serve_api,
            # Configuration commands
            ("config", "show"): handle_provider_config,
            ("config", "validate"): handle_validate_provider_config,
            ("config", "reload"): handle_reload_provider_config,
            # MCP commands - Function handlers
            ("mcp", "serve"): handle_mcp_serve,
            ("mcp", "tools", "list"): handle_mcp_tools_list,
            ("mcp", "tools", "call"): handle_mcp_tools_call,
            ("mcp", "tools", "info"): handle_mcp_tools_info,
            ("mcp", "validate"): handle_mcp_validate,
        }

        # All handlers are now async functions - no special handling needed
        if handler_key not in COMMAND_HANDLERS:
            raise ValueError(f"Unknown command: {args.resource} {args.action}")

        handler_func = COMMAND_HANDLERS[handler_key]

        if handler_func is None:
            raise NotImplementedError(f"Command not yet implemented: {args.resource} {args.action}")

        # All handlers are async functions with decorators
        result = await handler_func(args)
        return result

    finally:
        # Restore original scheduler if override was active
        if scheduler_override_active:
            try:
                app.config_manager.restore_scheduler_strategy()
            except Exception as e:
                from infrastructure.logging.logger import get_logger

                logger = get_logger(__name__)
                logger.warning("Failed to restore scheduler strategy: %s", e)


async def main() -> None:
    """Serve as main CLI entry point."""
    try:
        # Check if no arguments provided (except program name)
        if len(sys.argv) == 1:
            # No arguments provided, show help by adding --help to argv
            sys.argv.append("--help")

        # Parse arguments with systematic help display
        from io import StringIO

        # Capture stderr from the beginning to prevent duplicate usage lines
        old_stderr = sys.stderr
        sys.stderr = captured_stderr = StringIO()

        try:
            args, resource_parsers = parse_args()
            # Restore stderr on success
            sys.stderr = old_stderr
        except SystemExit as e:
            # Restore stderr
            sys.stderr = old_stderr
            error_output = captured_stderr.getvalue()

            # If it's an error and we have a resource, show clean help for that resource
            if e.code == 2 and len(sys.argv) >= 2 and "required: action" in error_output:
                resource_name = sys.argv[1]
                if resource_name in [
                    "templates",
                    "machines",
                    "requests",
                    "system",
                    "config",
                    "providers",
                    "storage",
                    "scheduler",
                ]:
                    # Show clean help without the error message
                    original_argv = sys.argv[:]
                    sys.argv = [sys.argv[0], resource_name, "--help"]
                    try:
                        parse_args()
                    except SystemExit as help_exit:
                        sys.argv = original_argv
                        if help_exit.code == 0:
                            sys.exit(0)
                    sys.argv = original_argv

            # For other errors, show the original error message and re-raise
            if error_output.strip():
                print(error_output.strip(), file=sys.stderr)  # noqa: CLI output
            raise

        # Handle completion generation
        if args.completion:
            if args.completion == "bash":
                print(generate_bash_completion())  # noqa: CLI output
            elif args.completion == "zsh":
                print(generate_zsh_completion())  # noqa: CLI output
            return

        # Configure logging - let the application's structured logging system handle
        # everything
        getattr(logging, args.log_level.upper())

        logger = get_logger(__name__)

        # Initialize application with dry-run mode if requested
        try:
            from bootstrap import Application

            app = Application(args.config)
            if not await app.initialize(dry_run=args.dry_run):
                raise RuntimeError("Failed to initialize application")
        except Exception as e:
            logger.error("Failed to initialize application: %s", e)
            if args.verbose:
                import traceback

                traceback.print_exc()
            sys.exit(1)

        # Execute command with dry-run context if requested
        try:
            # Import dry-run context
            from infrastructure.mocking.dry_run_context import dry_run_context

            # Execute command within dry-run context if flag is set
            if args.dry_run:
                logger.info("DRY-RUN mode activated - using mocked operations")
                with dry_run_context(True):
                    result = await execute_command(args, app)
            else:
                result = await execute_command(args, app)

            # Format and output result
            output_format = getattr(args, "format", None) or args.format
            formatted_output = format_output(result, output_format)

            if args.output:
                with open(args.output, "w") as f:
                    f.write(formatted_output)
                if not args.quiet:
                    print(f"Output written to {args.output}")  # noqa: CLI output
            else:
                print(formatted_output)  # noqa: CLI output

        except DomainException as e:
            logger.error("Domain error: %s", e)
            if not args.quiet:
                print(f"Error: {e}")  # noqa: CLI error
            sys.exit(1)
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            if args.verbose:
                import traceback

                traceback.print_exc()
            if not args.quiet:
                print(f"Unexpected error: {e}")  # noqa: CLI error
            sys.exit(1)

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")  # noqa: CLI output
        sys.exit(130)
    except Exception as e:
        print(f"Fatal error: {e}")  # noqa: CLI error
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
