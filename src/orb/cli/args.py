"""
CLI argument parsing module.

Handles all argument parser construction including global arguments,
resource-specific actions, and the top-level parse_args function.
"""

import argparse
import os
import sys

from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.value_objects import RequestStatus

# Optional: Rich formatting for help text
try:
    from rich_argparse import RichHelpFormatter  # type: ignore[import-untyped]

    HELP_FORMATTER = RichHelpFormatter
except ImportError:
    HELP_FORMATTER = argparse.RawDescriptionHelpFormatter


def add_global_arguments(parser):
    """Add arguments that should be available on all commands."""
    parser.add_argument(
        "-f",
        dest="hf_file",
        metavar="FILE",
        help="Input JSON file path (HostFactory compatibility)",
    )
    parser.add_argument(
        "-d",
        dest="hf_data",
        metavar="DATA",
        help="Input JSON data string (HostFactory compatibility)",
    )
    parser.add_argument(
        "--provider",
        help="Override provider instance (per-command flag, e.g. orb templates list --provider aws-prod)",
    )
    parser.add_argument("--region", help="AWS region override")
    parser.add_argument("--profile", help="AWS profile override")
    parser.add_argument(
        "--scheduler", choices=["default", "hostfactory"], help="Override scheduler strategy"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without executing")
    parser.add_argument("--yes", "-y", action="store_true", help="Assume yes to all prompts")
    parser.add_argument("--all", action="store_true", help="Apply to all resources")
    parser.add_argument(
        "--format", choices=["json", "yaml", "table", "list"], default="json", help="Output format"
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    parser.add_argument("--limit", type=int, help="Maximum number of results to return")
    parser.add_argument("--offset", type=int, default=0, help="Number of results to skip")
    parser.add_argument(
        "--filter",
        action="append",
        help="Generic filter using snake_case field names: field=value, field~value, field=~regex. Can be combined with specific filters. Use multiple times for AND logic.",
    )


def add_force_argument(parser):
    """Add --force argument for destructive operations."""
    parser.add_argument("--force", action="store_true", help="Force without confirmation")


def add_multi_provider_arguments(parser):
    """Add multi-provider arguments."""
    parser.add_argument("--all-providers", action="store_true", help="Apply to all providers")


def add_machine_actions(subparsers):
    """Add machine actions to a subparser."""
    machines_list = subparsers.add_parser(
        "list",
        help="List machines",
        description="List machines with filtering support. Use specific filters (--status, --request-id) or generic filters (--filter field=value).",
    )
    add_global_arguments(machines_list)
    machines_list.add_argument(
        "--status",
        choices=[s.value for s in MachineStatus],
        help="Filter by machine status (specific filter)",
    )
    machines_list.add_argument("--request-id", dest="request_id", help="Filter by request ID")
    machines_list.add_argument(
        "--timestamp-format",
        choices=["auto", "unix", "iso"],
        default="auto",
        help="Timestamp format: auto (scheduler default), unix (seconds), iso (ISO 8601)",
    )

    machines_show = subparsers.add_parser("show", help="Show machine details")
    add_global_arguments(machines_show)
    machines_show.add_argument("machine_id", nargs="?", help="Machine ID to show")
    machines_show.add_argument(
        "--machine-id", "-m", dest="flag_machine_id", help="Machine ID to show"
    )

    machines_request = subparsers.add_parser("request", help="Request machines")
    add_global_arguments(machines_request)
    machines_request.add_argument("template_id", nargs="?", help="Template ID to use")
    machines_request.add_argument(
        "machine_count", nargs="?", type=int, help="Number of machines to request"
    )
    machines_request.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to use"
    )
    machines_request.add_argument(
        "--count", "-c", type=int, dest="flag_machine_count", help="Number of machines to request"
    )
    machines_request.add_argument(
        "--wait", action="store_true", help="Wait for machines to be ready"
    )
    machines_request.add_argument(
        "--timeout", type=int, default=300, help="Wait timeout in seconds"
    )

    machines_return = subparsers.add_parser("return", help="Return machines")
    add_global_arguments(machines_return)
    add_force_argument(machines_return)
    machines_return.add_argument("machine_ids", nargs="*", help="Machine IDs to return")
    machines_return.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to return"
    )

    machines_terminate = subparsers.add_parser("terminate", help="Terminate (return) machines")
    add_global_arguments(machines_terminate)
    add_force_argument(machines_terminate)
    machines_terminate.add_argument("machine_ids", nargs="*", help="Machine IDs to terminate")
    machines_terminate.add_argument(
        "--machine-id",
        "-m",
        action="append",
        dest="machine_ids_flag",
        help="Machine ID to terminate",
    )

    machines_status = subparsers.add_parser("status", help="Check machine status")
    add_global_arguments(machines_status)
    machines_status.add_argument("machine_ids", nargs="*", help="Machine IDs to check")
    machines_status.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to check"
    )

    machines_stop = subparsers.add_parser("stop", help="Stop running machines")
    add_global_arguments(machines_stop)
    add_force_argument(machines_stop)
    machines_stop.add_argument("machine_ids", nargs="*", help="Machine IDs to stop")
    machines_stop.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to stop"
    )

    machines_start = subparsers.add_parser("start", help="Start stopped machines")
    add_global_arguments(machines_start)
    machines_start.add_argument("machine_ids", nargs="*", help="Machine IDs to start")
    machines_start.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to start"
    )


def add_request_actions(subparsers):
    """Add request actions to a subparser."""
    requests_list = subparsers.add_parser(
        "list",
        help="List requests",
        description="List requests with filtering support. Use specific filters (--status, --template-id) or generic filters (--filter field=value).",
    )
    add_global_arguments(requests_list)
    requests_list.add_argument(
        "--status",
        choices=[s.value for s in RequestStatus],
        help="Filter by request status (specific filter)",
    )
    requests_list.add_argument("--template-id", help="Filter by template ID (specific filter)")
    requests_list.add_argument(
        "--request-type",
        choices=["acquire", "return"],
        help="Filter by request type (specific filter)",
    )

    requests_show = subparsers.add_parser("show", help="Show request details")
    add_global_arguments(requests_show)
    requests_show.add_argument("request_id", nargs="?", help="Request ID to show")
    requests_show.add_argument(
        "--request-id", "-r", dest="flag_request_id", help="Request ID to show"
    )

    requests_cancel = subparsers.add_parser("cancel", help="Cancel request")
    add_global_arguments(requests_cancel)
    add_force_argument(requests_cancel)
    requests_cancel.add_argument("request_id", nargs="?", help="Request ID to cancel")
    requests_cancel.add_argument(
        "--request-id", "-r", dest="flag_request_id", help="Request ID to cancel"
    )

    requests_status = subparsers.add_parser("status", help="Check request status")
    add_global_arguments(requests_status)
    requests_status.add_argument("request_ids", nargs="*", help="Request IDs to check")
    requests_status.add_argument(
        "--request-id", "-r", action="append", dest="flag_request_ids", help="Request ID to check"
    )
    requests_status.add_argument(
        "--detailed", action="store_true", help="Show detailed request information"
    )

    requests_list_returns = subparsers.add_parser("list-returns", help="List return requests")
    add_global_arguments(requests_list_returns)
    requests_list_returns.add_argument(
        "--status", help="Filter by return request status"
    )


def add_infrastructure_actions(subparsers):
    """Add infrastructure actions to a subparser."""
    infra_discover = subparsers.add_parser(
        "discover",
        help="Scan AWS to find available infrastructure (VPCs, subnets, security groups)",
        description="Discover available infrastructure in your AWS account.",
    )
    add_global_arguments(infra_discover)
    add_multi_provider_arguments(infra_discover)
    infra_discover.add_argument(
        "--show",
        nargs="?",
        const="",
        help="Show only specific resources: vpcs,subnets,security-groups (or sg), or 'all' for everything",
    )
    infra_discover.add_argument(
        "--summary", action="store_true", help="Show only summary counts, no details"
    )

    infra_show = subparsers.add_parser(
        "show",
        help="Show current ORB infrastructure configuration",
        description="Display what infrastructure ORB is currently configured to use.",
    )
    add_global_arguments(infra_show)
    add_multi_provider_arguments(infra_show)

    infra_validate = subparsers.add_parser(
        "validate",
        help="Verify configured infrastructure still exists in AWS",
        description="Check if the infrastructure configured in ORB still exists in your AWS account.",
    )
    add_global_arguments(infra_validate)


def add_provider_actions(subparsers):
    """Add provider actions to a subparser."""
    providers_list = subparsers.add_parser(
        "list",
        help="List providers",
        description="List providers with filtering support.",
    )
    add_global_arguments(providers_list)
    providers_list.add_argument(
        "--detailed", action="store_true", help="Show detailed provider information"
    )

    providers_show = subparsers.add_parser("show", help="Show provider details")
    add_global_arguments(providers_show)
    providers_show.add_argument("provider_name", nargs="?", help="Provider name to show")

    providers_health = subparsers.add_parser("health", help="Check provider health")
    add_global_arguments(providers_health)
    providers_health.add_argument(
        "--detailed", action="store_true", help="Show detailed health information"
    )

    providers_add = subparsers.add_parser("add", help="Add new provider")
    add_global_arguments(providers_add)
    providers_add.add_argument(
        "--provider-type", dest="provider_type", required=True, help="Provider type (e.g. aws)"
    )
    providers_add.add_argument("--aws-profile", help="AWS profile name")
    providers_add.add_argument("--aws-region", help="AWS region")
    providers_add.add_argument("--name", help="Provider instance name")
    providers_add.add_argument("--discover", action="store_true", help="Discover infrastructure")

    providers_remove = subparsers.add_parser("remove", help="Remove provider")
    add_global_arguments(providers_remove)
    providers_remove.add_argument("provider_name", help="Provider instance name to remove")

    providers_update = subparsers.add_parser("update", help="Update provider configuration")
    add_global_arguments(providers_update)
    providers_update.add_argument("provider_name", help="Provider instance name")
    providers_update.add_argument("--aws-region", help="Update region")
    providers_update.add_argument("--aws-profile", help="Update profile")

    providers_set_default = subparsers.add_parser("set-default", help="Set default provider")
    add_global_arguments(providers_set_default)
    providers_set_default.add_argument("provider_name", help="Provider name to set as default")

    providers_get_default = subparsers.add_parser("get-default", help="Show default provider")
    add_global_arguments(providers_get_default)

    providers_select = subparsers.add_parser("select", help="Select provider instance")
    add_global_arguments(providers_select)
    providers_select.add_argument("provider_name", help="Provider name to select")
    providers_select.add_argument("--strategy", help="Specific strategy to select")

    providers_exec = subparsers.add_parser("exec", help="Execute provider command")
    add_global_arguments(providers_exec)
    providers_exec.add_argument("operation", help="Operation to execute")
    providers_exec.add_argument(
        "--params", "--args", dest="params", help="Operation parameters (JSON format)"
    )

    providers_metrics = subparsers.add_parser("metrics", help="Show provider metrics")
    add_global_arguments(providers_metrics)
    providers_metrics.add_argument(
        "--timeframe", default="1h", help="Metrics timeframe (e.g., 1h, 24h, 7d)"
    )


def add_template_actions(subparsers):
    """Add template actions to a subparser."""
    templates_list = subparsers.add_parser(
        "list",
        help="List templates",
        description="List templates with filtering support.",
    )
    add_global_arguments(templates_list)
    templates_list.add_argument("--provider-api", help="Filter by provider API type")

    templates_show = subparsers.add_parser("show", help="Show template details")
    add_global_arguments(templates_show)
    templates_show.add_argument("template_id", nargs="?", help="Template ID to show")
    templates_show.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to show"
    )

    templates_create = subparsers.add_parser("create", help="Create template")
    add_global_arguments(templates_create)
    templates_create.add_argument("--file", required=True, help="Template configuration file")
    templates_create.add_argument(
        "--validate-only", action="store_true", help="Only validate, do not create"
    )

    templates_update = subparsers.add_parser("update", help="Update template")
    add_global_arguments(templates_update)
    templates_update.add_argument("template_id", nargs="?", help="Template ID to update")
    templates_update.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to update"
    )
    templates_update.add_argument(
        "--file", required=True, help="Updated template configuration file"
    )

    templates_delete = subparsers.add_parser("delete", help="Delete template")
    add_global_arguments(templates_delete)
    add_force_argument(templates_delete)
    templates_delete.add_argument("template_id", nargs="?", help="Template ID to delete")
    templates_delete.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to delete"
    )

    templates_validate = subparsers.add_parser("validate", help="Validate template")
    add_global_arguments(templates_validate)
    templates_validate.add_argument("template_id", nargs="?", help="Template ID to validate")
    templates_validate.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to validate"
    )
    templates_validate.add_argument("--file", help="Template file to validate (pre-import)")

    templates_refresh = subparsers.add_parser("refresh", help="Refresh template cache")
    add_global_arguments(templates_refresh)
    add_force_argument(templates_refresh)

    templates_generate = subparsers.add_parser("generate", help="Generate example templates")
    add_global_arguments(templates_generate)
    add_force_argument(templates_generate)
    add_multi_provider_arguments(templates_generate)
    templates_generate.add_argument(
        "--provider-api", help="Provider API type (EC2Fleet, SpotFleet, ASG, RunInstances)"
    )
    templates_generate.add_argument(
        "--provider-specific",
        action="store_true",
        help="Generate templates with hardcoded infrastructure",
    )
    templates_generate.add_argument(
        "--generic", action="store_true", help="Generate generic templates"
    )
    templates_generate.add_argument("--provider-type", help="Provider type (e.g., aws)")


def build_parser() -> tuple[argparse.ArgumentParser, dict]:
    """Build the argument parser with resource-action structure.

    Returns:
        tuple: (parser, resource_parsers_dict)
    """
    from orb._package import DESCRIPTION, DOCS_URL

    parser = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description=DESCRIPTION,
        formatter_class=HELP_FORMATTER,
        epilog=f"""
Examples:
  %(prog)s templates list                              # List all templates
  %(prog)s templates list --provider aws-prod         # Use specific provider
  %(prog)s templates generate --all-providers         # Generate for all providers
  %(prog)s machines request template-id 5             # Request 5 machines
  %(prog)s machines list --filter "machine_types~t3"  # Filter machines by type
  %(prog)s requests status req-123                    # Check request status
  %(prog)s providers health --provider aws-prod       # Check provider health

For more information, visit: {DOCS_URL}
        """,
    )

    parser.add_argument("--config", help="Configuration file path")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set logging level",
    )
    parser.add_argument("--output", help="Output file (default: stdout)")
    parser.add_argument(
        "--completion", choices=["bash", "zsh"], help="Generate shell completion script"
    )
    parser.add_argument("-f", "--file", help="Input JSON file path (HostFactory compatibility)")
    parser.add_argument("-d", "--data", help="Input JSON data string (HostFactory compatibility)")

    try:
        from orb._package import __version__

        version_string = f"%(prog)s {__version__}"
    except ImportError:
        version_string = "%(prog)s develop"

    parser.add_argument("--version", action="version", version=version_string)

    subparsers = parser.add_subparsers(
        dest="resource", help="Available resources or legacy commands"
    )
    resource_parsers = {}

    # Templates
    templates_parser = subparsers.add_parser("templates", help="Compute templates")
    resource_parsers["templates"] = templates_parser
    templates_subparsers = templates_parser.add_subparsers(dest="action", help="Template actions")

    template_parser = subparsers.add_parser("template")
    resource_parsers["template"] = template_parser
    template_subparsers = template_parser.add_subparsers(dest="action", help="Template actions")

    add_template_actions(templates_subparsers)
    add_template_actions(template_subparsers)

    # Machines
    machines_parser = subparsers.add_parser("machines", help="Compute instances")
    resource_parsers["machines"] = machines_parser
    machines_subparsers = machines_parser.add_subparsers(dest="action", help="Machine actions")

    machine_parser = subparsers.add_parser("machine")
    resource_parsers["machine"] = machine_parser
    machine_subparsers = machine_parser.add_subparsers(dest="action", help="Machine actions")

    add_machine_actions(machines_subparsers)
    add_machine_actions(machine_subparsers)

    # Requests
    requests_parser = subparsers.add_parser("requests", help="Provisioning requests")
    resource_parsers["requests"] = requests_parser
    requests_subparsers = requests_parser.add_subparsers(dest="action", help="Request actions")

    request_parser = subparsers.add_parser("request")
    resource_parsers["request"] = request_parser
    request_subparsers = request_parser.add_subparsers(dest="action", help="Request actions")

    add_request_actions(requests_subparsers)
    add_request_actions(request_subparsers)

    # System
    system_parser = subparsers.add_parser("system", help="System operations")
    resource_parsers["system"] = system_parser
    system_subparsers = system_parser.add_subparsers(
        dest="action", help="System actions", required=True
    )

    system_status = system_subparsers.add_parser("status", help="Show system status")
    add_global_arguments(system_status)

    system_health = system_subparsers.add_parser("health", help="Check system health")
    add_global_arguments(system_health)
    system_health.add_argument(
        "--detailed", action="store_true", help="Show detailed health information"
    )

    system_metrics = system_subparsers.add_parser("metrics", help="Show system metrics")
    add_global_arguments(system_metrics)

    system_serve = system_subparsers.add_parser("serve", help="Start REST API server")
    add_global_arguments(system_serve)
    system_serve.add_argument("--host", default="0.0.0.0", help="Server host")  # nosec B104 - intentional default, overridable via CLI flag
    system_serve.add_argument("--port", type=int, default=8000, help="Server port")
    system_serve.add_argument("--workers", type=int, default=1, help="Number of workers")
    system_serve.add_argument("--reload", action="store_true", help="Enable auto-reload")
    system_serve.add_argument("--server-log-level", default="info", help="Server log level")

    # Infrastructure
    infrastructure_parser = subparsers.add_parser("infrastructure", help="Infrastructure discovery")
    resource_parsers["infrastructure"] = infrastructure_parser
    infrastructure_subparsers = infrastructure_parser.add_subparsers(
        dest="action", help="Infrastructure actions"
    )

    infra_parser = subparsers.add_parser("infra")
    resource_parsers["infra"] = infra_parser
    infra_subparsers = infra_parser.add_subparsers(dest="action", help="Infrastructure actions")

    add_infrastructure_actions(infrastructure_subparsers)
    add_infrastructure_actions(infra_subparsers)

    # Config
    config_parser = subparsers.add_parser("config", help="Configuration")
    resource_parsers["config"] = config_parser
    config_subparsers = config_parser.add_subparsers(
        dest="action", help="Config actions", required=True
    )

    config_show = config_subparsers.add_parser("show", help="Show configuration")
    add_global_arguments(config_show)

    config_set = config_subparsers.add_parser("set", help="Set configuration")
    add_global_arguments(config_set)
    config_set.add_argument("key", help="Configuration key")
    config_set.add_argument("value", help="Configuration value")

    config_get = config_subparsers.add_parser("get", help="Get configuration")
    add_global_arguments(config_get)
    config_get.add_argument("key", help="Configuration key")

    config_validate = config_subparsers.add_parser("validate", help="Validate configuration")
    add_global_arguments(config_validate)
    config_validate.add_argument("--file", help="Configuration file to validate")

    # Providers
    providers_parser = subparsers.add_parser("providers", help="Cloud providers")
    resource_parsers["providers"] = providers_parser
    providers_subparsers = providers_parser.add_subparsers(dest="action", help="Provider actions")

    provider_parser = subparsers.add_parser("provider")
    resource_parsers["provider"] = provider_parser
    provider_subparsers = provider_parser.add_subparsers(dest="action", help="Provider actions")

    add_provider_actions(providers_subparsers)
    add_provider_actions(provider_subparsers)

    # Storage
    storage_parser = subparsers.add_parser("storage", help="Storage")
    resource_parsers["storage"] = storage_parser
    storage_subparsers = storage_parser.add_subparsers(
        dest="action", help="Storage actions", required=True
    )

    storage_list = storage_subparsers.add_parser("list", help="List storage strategies")
    add_global_arguments(storage_list)

    storage_show = storage_subparsers.add_parser("show", help="Show storage configuration")
    add_global_arguments(storage_show)
    storage_show.add_argument("--strategy", help="Show specific storage strategy details")

    storage_validate = storage_subparsers.add_parser("validate", help="Validate storage")
    add_global_arguments(storage_validate)
    storage_validate.add_argument("--strategy", help="Validate specific storage strategy")

    storage_test = storage_subparsers.add_parser("test", help="Test storage connectivity")
    add_global_arguments(storage_test)
    storage_test.add_argument("--strategy", help="Test specific storage strategy")
    storage_test.add_argument("--timeout", type=int, default=30, help="Test timeout in seconds")

    storage_health = storage_subparsers.add_parser("health", help="Check storage health")
    add_global_arguments(storage_health)
    storage_health.add_argument(
        "--detailed", action="store_true", help="Show detailed health information"
    )

    storage_metrics = storage_subparsers.add_parser("metrics", help="Show storage metrics")
    add_global_arguments(storage_metrics)
    storage_metrics.add_argument("--strategy", help="Show metrics for specific storage strategy")

    # Scheduler
    scheduler_parser = subparsers.add_parser("scheduler", help="Scheduler")
    resource_parsers["scheduler"] = scheduler_parser
    scheduler_subparsers = scheduler_parser.add_subparsers(
        dest="action", help="Scheduler actions", required=True
    )

    scheduler_list = scheduler_subparsers.add_parser("list", help="List scheduler strategies")
    add_global_arguments(scheduler_list)

    scheduler_show = scheduler_subparsers.add_parser("show", help="Show scheduler details")
    add_global_arguments(scheduler_show)
    scheduler_show.add_argument("--strategy", help="Show specific scheduler strategy details")

    scheduler_validate = scheduler_subparsers.add_parser("validate", help="Validate scheduler")
    add_global_arguments(scheduler_validate)
    scheduler_validate.add_argument("--strategy", help="Validate specific scheduler strategy")

    # MCP
    mcp_parser = subparsers.add_parser("mcp", help="MCP (Model Context Protocol) operations")
    resource_parsers["mcp"] = mcp_parser
    mcp_subparsers = mcp_parser.add_subparsers(dest="action", help="MCP actions", required=True)

    mcp_tools = mcp_subparsers.add_parser("tools", help="MCP tools management")
    mcp_tools_sub = mcp_tools.add_subparsers(dest="tools_action", required=True)

    mcp_tools_list = mcp_tools_sub.add_parser("list", help="List MCP tools")
    add_global_arguments(mcp_tools_list)
    mcp_tools_list.add_argument(
        "--type", choices=["command", "query"], help="Filter tools by handler type"
    )

    mcp_tools_call = mcp_tools_sub.add_parser("call", help="Call MCP tool")
    add_global_arguments(mcp_tools_call)
    mcp_tools_call.add_argument("tool_name", help="Name of tool to call")
    mcp_tools_call.add_argument("--args", help="Tool arguments as JSON string")
    mcp_tools_call.add_argument("--file", help="Tool arguments from JSON file")

    mcp_tools_info = mcp_tools_sub.add_parser("info", help="Show MCP tool details")
    add_global_arguments(mcp_tools_info)
    mcp_tools_info.add_argument("tool_name", help="Name of tool to get info for")

    mcp_validate = mcp_subparsers.add_parser("validate", help="Validate MCP")
    add_global_arguments(mcp_validate)
    mcp_validate.add_argument("--config", help="MCP configuration file to validate")

    mcp_serve = mcp_subparsers.add_parser("serve", help="Start MCP server")
    add_global_arguments(mcp_serve)
    mcp_serve.add_argument("--port", type=int, default=3000, help="Server port (default: 3000)")
    mcp_serve.add_argument("--host", default="localhost", help="Server host (default: localhost)")
    mcp_serve.add_argument(
        "--stdio", action="store_true", help="Run in stdio mode for direct MCP client communication"
    )
    mcp_serve.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level for MCP server",
    )

    # Init
    init_parser = subparsers.add_parser("init", help="Initialize ORB configuration")
    add_force_argument(init_parser)
    init_parser.add_argument("--non-interactive", action="store_true", help="Non-interactive mode")
    init_parser.add_argument(
        "--scheduler", choices=["default", "hostfactory"], help="Scheduler type"
    )
    init_parser.add_argument("--provider", default="aws", help="Provider type")
    init_parser.add_argument("--region", help="AWS region")
    init_parser.add_argument("--profile", help="AWS profile")
    init_parser.add_argument("--config-dir", help="Custom configuration directory")
    init_parser.add_argument(
        "--subnet-ids",
        help="Comma-separated subnet IDs for template_defaults (non-interactive only)",
    )
    init_parser.add_argument(
        "--security-group-ids",
        help="Comma-separated security group IDs for template_defaults (non-interactive only)",
    )
    init_parser.add_argument(
        "--fleet-role",
        help="Spot Fleet IAM role ARN or name for template_defaults (non-interactive only)",
    )

    return parser, resource_parsers


def parse_args() -> tuple[argparse.Namespace, dict]:
    """Parse command line arguments. Thin wrapper around build_parser()."""
    parser, resource_parsers = build_parser()
    return parser.parse_args(), resource_parsers
