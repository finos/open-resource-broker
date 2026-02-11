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
from typing import Any, Union

from _package import DOCS_URL
from cli.completion import generate_bash_completion, generate_zsh_completion
from cli.formatters import format_output
from domain.base.exceptions import DomainException
from domain.request.value_objects import RequestStatus
from infrastructure.logging.logger import get_logger

# Optional: Rich formatting for help text
try:
    from rich_argparse import RichHelpFormatter

    HELP_FORMATTER = RichHelpFormatter
except ImportError:
    HELP_FORMATTER = argparse.RawDescriptionHelpFormatter


def add_global_arguments(parser):
    """
    Add arguments that should be available on all commands.
    
    Filtering Strategy:
    - Generic filters: --filter field=value (works on any snake_case field)
    - Specific filters: Command-specific filters like --status, --template-id
    - Both can be combined: --filter "machine_types~t3" --status running
    - Multiple generic filters use AND logic: --filter "machine_types~t3" --filter "status=running"
    
    Common filter examples:
    - --filter "machine_types~t3"           # Templates with t3 instance types
    - --filter "machine_types~medium"       # Templates with medium-sized instances
    - --filter "status=running"             # Running machines/requests
    - --filter "template_id~instant"        # Templates with "instant" in ID
    """
    # Provider and environment overrides
    parser.add_argument("--provider", help="Override provider instance")
    parser.add_argument("--region", help="AWS region override")
    parser.add_argument("--profile", help="AWS profile override")
    parser.add_argument("--scheduler", choices=["default", "hostfactory", "hf"], help="Override scheduler strategy")
    
    # Operation control
    parser.add_argument("--dry-run", action="store_true", help="Preview without executing")
    parser.add_argument("--yes", "-y", action="store_true", help="Assume yes to all prompts")
    parser.add_argument("--timeout", type=int, help="Operation timeout in seconds")
    
    # Output control
    parser.add_argument("--format", choices=["json", "yaml", "table", "list"], default="json", help="Output format")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    
    # Pagination and filtering
    parser.add_argument("--limit", type=int, help="Maximum number of results to return")
    parser.add_argument("--offset", type=int, default=0, help="Number of results to skip")
    parser.add_argument("--filter", action="append", help="Generic filter using snake_case field names: field=value, field~value, field=~regex. Examples: --filter \"machine_types~t3\", --filter \"status=running\". Can be combined with specific filters. Use multiple times for AND logic.")


def add_force_argument(parser):
    """Add --force argument for destructive operations."""
    parser.add_argument("--force", action="store_true", help="Force without confirmation")


def add_multi_provider_arguments(parser):
    """Add multi-provider arguments."""
    parser.add_argument("--all-providers", action="store_true", help="Apply to all providers")


def add_machine_actions(subparsers):
    """Add machine actions to a subparser."""
    # Machines list
    machines_list = subparsers.add_parser(
        "list", 
        help="List machines",
        description="List machines with filtering support. Use specific filters (--status, --template-id) or generic filters (--filter field=value)."
    )
    add_global_arguments(machines_list)
    machines_list.add_argument("--status", help="Filter by machine status (specific filter)")
    machines_list.add_argument("--template-id", help="Filter by template ID (specific filter)")
    machines_list.add_argument("--timestamp-format", choices=["auto", "unix", "iso"], default="auto", help="Timestamp format: auto (scheduler default), unix (seconds), iso (ISO 8601)")

    # Machines show
    machines_show = subparsers.add_parser("show", help="Show machine details")
    add_global_arguments(machines_show)
    machines_show.add_argument("machine_id", nargs="?", help="Machine ID to show")
    machines_show.add_argument(
        "--machine-id", "-m", dest="flag_machine_id", help="Machine ID to show"
    )

    # Machines request (create machines)
    machines_request = subparsers.add_parser("request", help="Request machines")
    add_global_arguments(machines_request)
    machines_request.add_argument(
        "template_id",
        nargs="?",
        help="Template ID to use",
    )
    machines_request.add_argument(
        "machine_count",
        nargs="?",
        type=int,
        help="Number of machines to request",
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

    # Machines return (terminate machines)
    machines_return = subparsers.add_parser("return", help="Return machines")
    add_global_arguments(machines_return)
    add_force_argument(machines_return)
    machines_return.add_argument(
        "machine_ids", nargs="*", help="Machine IDs to return"
    )

    # Machines status
    machines_status = subparsers.add_parser("status", help="Check machine status")
    add_global_arguments(machines_status)
    machines_status.add_argument("machine_ids", nargs="+", help="Machine IDs to check")


def add_request_actions(subparsers):
    """Add request actions to a subparser."""
    # Requests list
    requests_list = subparsers.add_parser(
        "list", 
        help="List requests",
        description="List requests with filtering support. Use specific filters (--status, --template-id) or generic filters (--filter field=value)."
    )
    add_global_arguments(requests_list)
    requests_list.add_argument(
        "--status",
        choices=[s.value for s in RequestStatus],
        help="Filter by request status (specific filter)",
    )
    requests_list.add_argument("--template-id", help="Filter by template ID (specific filter)")

    # Requests show
    requests_show = subparsers.add_parser("show", help="Show request details")
    add_global_arguments(requests_show)
    requests_show.add_argument("request_id", help="Request ID to show")

    # Requests cancel
    requests_cancel = subparsers.add_parser("cancel", help="Cancel request")
    add_global_arguments(requests_cancel)
    add_force_argument(requests_cancel)
    requests_cancel.add_argument("request_id", help="Request ID to cancel")

    # Requests status
    requests_status = subparsers.add_parser("status", help="Check request status")
    add_global_arguments(requests_status)
    requests_status.add_argument("request_ids", nargs="*", help="Request IDs to check")
    requests_status.add_argument(
        "--request-id",
        "-r",
        action="append",
        dest="flag_request_ids",
        help="Request ID to check",
    )


async def _show_resource_help(resource):
    """Show help for a resource when no action is provided."""
    import subprocess
    import sys
    
    # Call the CLI with --help for the specific resource
    subprocess.run([sys.executable, "-m", "run", resource, "--help"])
    return {"success": True, "message": f"Showed help for {resource}"}


async def _show_templates_help(args):
    """Show templates help."""
    return await _show_resource_help("templates")


async def _show_machines_help(args):
    """Show machines help."""
    return await _show_resource_help("machines")


async def _show_requests_help(args):
    """Show requests help."""
    return await _show_resource_help("requests")


async def _show_providers_help(args):
    """Show providers help."""
    return await _show_resource_help("providers")


def add_infrastructure_actions(subparsers):
    """Add infrastructure actions to a subparser."""
    # Infrastructure discover
    infra_discover = subparsers.add_parser(
        "discover", 
        help="Scan AWS to find available infrastructure (VPCs, subnets, security groups)",
        description="Discover available infrastructure in your AWS account. Makes AWS API calls to find VPCs, subnets, and security groups you can use."
    )
    add_global_arguments(infra_discover)
    add_multi_provider_arguments(infra_discover)
    infra_discover.add_argument("--show", nargs='?', const='', help="Show only specific resources: vpcs,subnets,security-groups (or sg), or 'all' for everything")
    infra_discover.add_argument("--all", action="store_true", help="Show all resources without truncation")
    infra_discover.add_argument("--summary", action="store_true", help="Show only summary counts, no details")

    # Infrastructure show
    infra_show = subparsers.add_parser(
        "show", 
        help="Show current ORB infrastructure configuration",
        description="Display what infrastructure ORB is currently configured to use (from template_defaults in config)."
    )
    add_global_arguments(infra_show)
    add_multi_provider_arguments(infra_show)

    # Infrastructure validate
    infra_validate = subparsers.add_parser(
        "validate", 
        help="Verify configured infrastructure still exists in AWS",
        description="Check if the infrastructure configured in ORB (template_defaults) still exists in your AWS account."
    )
    add_global_arguments(infra_validate)


def add_provider_actions(subparsers):
    """Add provider actions to a subparser."""
    # Providers list
    providers_list = subparsers.add_parser(
        "list", 
        help="List providers",
        description="List providers with filtering support. Use specific filters (--detailed) or generic filters (--filter field=value)."
    )
    add_global_arguments(providers_list)
    providers_list.add_argument(
        "--detailed", action="store_true", help="Show detailed provider information (specific filter)"
    )

    # Providers show
    providers_show = subparsers.add_parser("show", help="Show provider details")
    add_global_arguments(providers_show)

    # Providers health
    providers_health = subparsers.add_parser("health", help="Check provider health")
    add_global_arguments(providers_health)

    # Providers select
    providers_select = subparsers.add_parser("select", help="Select provider strategy")
    add_global_arguments(providers_select)
    providers_select.add_argument("provider", help="Provider name to select")
    providers_select.add_argument("--strategy", help="Specific strategy to select")

    # Providers exec
    providers_exec = subparsers.add_parser("exec", help="Execute provider operation")
    add_global_arguments(providers_exec)
    providers_exec.add_argument("operation", help="Operation to execute")
    providers_exec.add_argument("--params", help="Operation parameters (JSON format)")

    # Providers metrics
    providers_metrics = subparsers.add_parser("metrics", help="Show provider metrics")
    add_global_arguments(providers_metrics)


def add_template_actions(subparsers):
    """Add template actions to a subparser."""
    # Templates list
    templates_list = subparsers.add_parser(
        "list", 
        help="List templates",
        description="List templates with filtering support. Use specific filters (--provider-api) or generic filters (--filter field=value)."
    )
    add_global_arguments(templates_list)
    templates_list.add_argument("--provider-api", help="Filter by provider API type (specific filter)")
    templates_list.add_argument(
        "--long", action="store_true", help="Include detailed configuration fields"
    )

    # Templates show
    templates_show = subparsers.add_parser("show", help="Show template details")
    add_global_arguments(templates_show)
    templates_show.add_argument("template_id", nargs="?", help="Template ID to show")
    templates_show.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to show"
    )

    # Templates create
    templates_create = subparsers.add_parser("create", help="Create template")
    add_global_arguments(templates_create)
    templates_create.add_argument("--file", required=True, help="Template configuration file")
    templates_create.add_argument(
        "--validate-only", action="store_true", help="Only validate, do not create"
    )

    # Templates update
    templates_update = subparsers.add_parser("update", help="Update template")
    add_global_arguments(templates_update)
    templates_update.add_argument("template_id", nargs="?", help="Template ID to update")
    templates_update.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to update"
    )
    templates_update.add_argument(
        "--file", required=True, help="Updated template configuration file"
    )

    # Templates delete
    templates_delete = subparsers.add_parser("delete", help="Delete template")
    add_global_arguments(templates_delete)
    add_force_argument(templates_delete)
    templates_delete.add_argument("template_id", nargs="?", help="Template ID to delete")
    templates_delete.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to delete"
    )

    # Templates validate
    templates_validate = subparsers.add_parser("validate", help="Validate template")
    add_global_arguments(templates_validate)
    templates_validate.add_argument("--file", required=True, help="Template file to validate")

    # Templates refresh
    templates_refresh = subparsers.add_parser("refresh", help="Refresh template cache")
    add_global_arguments(templates_refresh)
    add_force_argument(templates_refresh)

    # Templates generate
    templates_generate = subparsers.add_parser(
        "generate", help="Generate example templates"
    )
    add_global_arguments(templates_generate)
    add_force_argument(templates_generate)
    add_multi_provider_arguments(templates_generate)
    templates_generate.add_argument(
        "--provider-api", help="Provider API type (EC2Fleet, SpotFleet, ASG, RunInstances)"
    )
    templates_generate.add_argument(
        "--provider-specific", action="store_true", help="Generate templates with hardcoded infrastructure"
    )
    templates_generate.add_argument(
        "--generic", action="store_true", help="Generate generic templates (same as default behavior)"
    )
    templates_generate.add_argument(
        "--provider-type", help="Provider type (e.g., aws)"
    )


def parse_args() -> tuple[argparse.Namespace, dict]:
    """Parse command line arguments with resource-action structure.

    Returns:
        tuple: (parsed_args, resource_parsers_dict)
    """

    # Main parser with global options
    parser = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description="Open Resource Broker - Cloud resource management for IBM Spectrum Symphony. Use --filter with snake_case field names (e.g., machine_types~t3) for generic filtering.",
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

    # System-level options only
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
    templates_parser = subparsers.add_parser("templates", help="Compute templates")
    resource_parsers["templates"] = templates_parser
    templates_subparsers = templates_parser.add_subparsers(
        dest="action", help="Template actions"
    )

    # Template resource (singular alias - hidden from main help)
    template_parser = subparsers.add_parser("template")
    resource_parsers["template"] = template_parser
    template_subparsers = template_parser.add_subparsers(
        dest="action", help="Template actions"
    )

    # Add actions to both plural and singular forms
    add_template_actions(templates_subparsers)
    add_template_actions(template_subparsers)

    # Machines resource
    machines_parser = subparsers.add_parser("machines", help="Compute instances")
    resource_parsers["machines"] = machines_parser
    machines_subparsers = machines_parser.add_subparsers(
        dest="action", help="Machine actions"
    )

    # Machine resource (singular alias - hidden from main help)
    machine_parser = subparsers.add_parser("machine")
    resource_parsers["machine"] = machine_parser
    machine_subparsers = machine_parser.add_subparsers(
        dest="action", help="Machine actions"
    )

    # Add actions to both plural and singular forms
    add_machine_actions(machines_subparsers)
    add_machine_actions(machine_subparsers)

    # Requests resource
    requests_parser = subparsers.add_parser("requests", help="Provisioning requests")
    resource_parsers["requests"] = requests_parser
    requests_subparsers = requests_parser.add_subparsers(
        dest="action", help="Request actions"
    )

    # Request resource (singular alias - hidden from main help)
    request_parser = subparsers.add_parser("request")
    resource_parsers["request"] = request_parser
    request_subparsers = request_parser.add_subparsers(
        dest="action", help="Request actions"
    )

    # Add actions to both plural and singular forms
    add_request_actions(requests_subparsers)
    add_request_actions(request_subparsers)

    # System resource
    system_parser = subparsers.add_parser("system", help="System operations")
    resource_parsers["system"] = system_parser
    system_subparsers = system_parser.add_subparsers(
        dest="action", help="System actions", required=True
    )

    # System status
    system_status = system_subparsers.add_parser("status", help="Show system status")
    add_global_arguments(system_status)

    # System health
    system_health = system_subparsers.add_parser("health", help="Check system health")
    add_global_arguments(system_health)
    system_health.add_argument(
        "--detailed", action="store_true", help="Show detailed health information"
    )

    # System metrics
    system_metrics = system_subparsers.add_parser("metrics", help="Show system metrics")
    add_global_arguments(system_metrics)

    # System serve
    system_serve = system_subparsers.add_parser("serve", help="Start REST API server")
    system_serve.add_argument("--host", default="0.0.0.0", help="Server host")  # nosec B104
    system_serve.add_argument("--port", type=int, default=8000, help="Server port")
    system_serve.add_argument("--workers", type=int, default=1, help="Number of workers")
    system_serve.add_argument("--reload", action="store_true", help="Enable auto-reload")
    system_serve.add_argument("--server-log-level", default="info", help="Server log level")

    # Infrastructure resource
    infrastructure_parser = subparsers.add_parser("infrastructure", help="Infrastructure discovery")
    resource_parsers["infrastructure"] = infrastructure_parser
    infrastructure_subparsers = infrastructure_parser.add_subparsers(
        dest="action", help="Infrastructure actions"
    )

    # Infra resource (shortcut alias - hidden from main help)
    infra_parser = subparsers.add_parser("infra")
    resource_parsers["infra"] = infra_parser
    infra_subparsers = infra_parser.add_subparsers(
        dest="action", help="Infrastructure actions"
    )

    # Add actions to both full and shortcut forms
    add_infrastructure_actions(infrastructure_subparsers)
    add_infrastructure_actions(infra_subparsers)

    # Config resource
    config_parser = subparsers.add_parser("config", help="Configuration")
    resource_parsers["config"] = config_parser
    config_subparsers = config_parser.add_subparsers(
        dest="action", help="Config actions", required=True
    )

    # Config show
    config_show = config_subparsers.add_parser("show", help="Show configuration")
    add_global_arguments(config_show)

    # Config set
    config_set = config_subparsers.add_parser("set", help="Set configuration")
    add_global_arguments(config_set)
    config_set.add_argument("key", help="Configuration key")
    config_set.add_argument("value", help="Configuration value")

    # Config get
    config_get = config_subparsers.add_parser("get", help="Get configuration")
    add_global_arguments(config_get)
    config_get.add_argument("key", help="Configuration key")

    # Config validate
    config_validate = config_subparsers.add_parser("validate", help="Validate configuration")
    add_global_arguments(config_validate)
    config_validate.add_argument("--file", help="Configuration file to validate")

    # Providers resource
    providers_parser = subparsers.add_parser("providers", help="Cloud providers")
    resource_parsers["providers"] = providers_parser
    providers_subparsers = providers_parser.add_subparsers(
        dest="action", help="Provider actions"
    )

    # Provider resource (singular alias - hidden from main help)
    provider_parser = subparsers.add_parser("provider")
    resource_parsers["provider"] = provider_parser
    provider_subparsers = provider_parser.add_subparsers(
        dest="action", help="Provider actions"
    )

    # Add actions to both plural and singular forms
    add_provider_actions(providers_subparsers)
    add_provider_actions(provider_subparsers)

    # Storage resource
    storage_parser = subparsers.add_parser("storage", help="Storage")
    resource_parsers["storage"] = storage_parser
    storage_subparsers = storage_parser.add_subparsers(
        dest="action", help="Storage actions", required=True
    )

    # Storage list
    storage_list = storage_subparsers.add_parser(
        "list", 
        help="List storage strategies",
        description="List storage strategies with filtering support using generic filters (--filter field=value)."
    )
    add_global_arguments(storage_list)

    # Storage show
    storage_show = storage_subparsers.add_parser("show", help="Show storage configuration")
    add_global_arguments(storage_show)
    storage_show.add_argument("--strategy", help="Show specific storage strategy details")

    # Storage validate
    storage_validate = storage_subparsers.add_parser("validate", help="Validate storage")
    add_global_arguments(storage_validate)
    storage_validate.add_argument("--strategy", help="Validate specific storage strategy")

    # Storage test
    storage_test = storage_subparsers.add_parser("test", help="Test storage connectivity")
    add_global_arguments(storage_test)
    storage_test.add_argument("--strategy", help="Test specific storage strategy")
    storage_test.add_argument("--timeout", type=int, default=30, help="Test timeout in seconds")

    # Storage health
    storage_health = storage_subparsers.add_parser("health", help="Check storage health")
    add_global_arguments(storage_health)
    storage_health.add_argument(
        "--detailed", action="store_true", help="Show detailed health information"
    )

    # Storage metrics
    storage_metrics = storage_subparsers.add_parser("metrics", help="Show storage metrics")
    add_global_arguments(storage_metrics)
    storage_metrics.add_argument("--strategy", help="Show metrics for specific storage strategy")

    # Scheduler resource
    scheduler_parser = subparsers.add_parser("scheduler", help="Scheduler")
    resource_parsers["scheduler"] = scheduler_parser
    scheduler_subparsers = scheduler_parser.add_subparsers(
        dest="action", help="Scheduler actions", required=True
    )

    # Scheduler list
    scheduler_list = scheduler_subparsers.add_parser(
        "list", 
        help="List scheduler strategies",
        description="List scheduler strategies with filtering support using generic filters (--filter field=value)."
    )
    add_global_arguments(scheduler_list)
    scheduler_list.add_argument("--long", action="store_true", help="Show detailed information")

    # Scheduler show
    scheduler_show = scheduler_subparsers.add_parser("show", help="Show scheduler details")
    add_global_arguments(scheduler_show)
    scheduler_show.add_argument("--strategy", help="Show specific scheduler strategy details")

    # Scheduler validate
    scheduler_validate = scheduler_subparsers.add_parser("validate", help="Validate scheduler")
    add_global_arguments(scheduler_validate)
    scheduler_validate.add_argument("--strategy", help="Validate specific scheduler strategy")

    # MCP resource
    mcp_parser = subparsers.add_parser("mcp", help="MCP (Model Context Protocol) operations")
    resource_parsers["mcp"] = mcp_parser
    mcp_subparsers = mcp_parser.add_subparsers(dest="action", help="MCP actions", required=True)

    # MCP tools
    mcp_tools = mcp_subparsers.add_parser("tools", help="MCP tools management")
    mcp_tools_sub = mcp_tools.add_subparsers(dest="tools_action", required=True)

    # MCP tools list
    mcp_tools_list = mcp_tools_sub.add_parser("list", help="List MCP tools")
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
    mcp_tools_call = mcp_tools_sub.add_parser("call", help="Call MCP tool")
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
    mcp_tools_info = mcp_tools_sub.add_parser("info", help="Show MCP tool details")
    mcp_tools_info.add_argument("tool_name", help="Name of tool to get info for")
    mcp_tools_info.add_argument(
        "--format",
        choices=["json", "yaml", "table"],
        default="table",
        help="Output format",
    )

    # MCP validate
    mcp_validate = mcp_subparsers.add_parser("validate", help="Validate MCP")
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

    # Init command
    init_parser = subparsers.add_parser("init", help="Initialize ORB configuration")
    init_parser.add_argument("--non-interactive", action="store_true", help="Non-interactive mode")
    init_parser.add_argument("--force", action="store_true", help="Force overwrite existing config")
    init_parser.add_argument(
        "--scheduler", choices=["default", "hostfactory"], help="Scheduler type"
    )
    init_parser.add_argument("--provider", default="aws", help="Provider type")
    init_parser.add_argument("--region", help="AWS region")
    init_parser.add_argument("--profile", help="AWS profile")
    init_parser.add_argument("--config-dir", help="Custom configuration directory")

    return parser.parse_args(), resource_parsers


async def execute_command(args, app, resource_parsers) -> Union[str, tuple[str, int]]:
    """Execute command using pure CQRS pattern."""
    # Process input data from -f/--file or -d/--data flags
    input_data = None
    if hasattr(args, "file") and args.file:
        try:
            import json
            with open(args.file) as f:
                input_data = json.load(f)
        except Exception as e:
            raise DomainException(f"Failed to load input file: {e}")
    elif hasattr(args, "data") and args.data:
        try:
            import json
            input_data = json.loads(args.data)
        except Exception as e:
            raise DomainException(f"Failed to parse input data: {e}")

    args.input_data = input_data

    # Handle special cases that return direct results
    if args.resource == "init":
        from interface.init_command_handler import handle_init
        result = await handle_init(args)
        
        # Format using response formatter
        from cli.response_formatter import create_cli_formatter
        formatter = create_cli_formatter()
        return formatter.format_response(result, args)
    
    if args.resource == "mcp" and args.action == "serve":
        from interface.mcp.server.handler import handle_mcp_serve
        result = await handle_mcp_serve(args)
        
        # Format using response formatter
        from cli.response_formatter import create_cli_formatter
        formatter = create_cli_formatter()
        return formatter.format_response(result, args)

    # Use pure CQRS pattern for all other commands
    from infrastructure.di.container import get_container
    from infrastructure.di.buses import CommandBus, QueryBus
    from domain.base.ports.scheduler_port import SchedulerPort
    from cli.command_factory import cli_command_factory
    from application.interfaces.command_query import Command
    from cli.response_formatter import create_cli_formatter
    
    container = get_container()
    command_bus = container.get(CommandBus)
    query_bus = container.get(QueryBus)
    scheduler_port = container.get(SchedulerPort)
    
    # Handle infrastructure commands directly (not through CQRS)
    if hasattr(args, 'resource') and args.resource in ['infrastructure', 'infra']:
        from interface.infrastructure_command_handler import (
            handle_infrastructure_discover,
            handle_infrastructure_show, 
            handle_infrastructure_validate
        )
        
        if args.action == 'discover':
            result = await handle_infrastructure_discover(args)
        elif args.action == 'show':
            result = await handle_infrastructure_show(args)
        elif args.action == 'validate':
            result = await handle_infrastructure_validate(args)
        else:
            raise ValueError(f"Unknown infrastructure action: {args.action}")
    else:
        # Create command or query from CLI args
        command_or_query = cli_command_factory.create_command_or_query(args)
        
        # Execute through appropriate bus
        from application.dto.base import BaseCommand
        if isinstance(command_or_query, (Command, BaseCommand)):
            result = await command_bus.execute(command_or_query)
        else:
            result = await query_bus.execute(command_or_query)
    
    # Format response for CLI output using improved formatter
    formatter = create_cli_formatter(scheduler_port)
    return formatter.format_response(result, args)





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
                print(error_output.strip(), file=sys.stderr)
            raise

        # Handle completion generation
        if args.completion:
            if args.completion == "bash":
                print(generate_bash_completion())
            elif args.completion == "zsh":
                print(generate_zsh_completion())
            return

        # Configure logging - let the application's structured logging system handle
        # everything
        getattr(logging, args.log_level.upper())

        logger = get_logger(__name__)

        # Handle help display early - no need for app initialization
        if hasattr(args, 'action') and args.action is None and args.resource in ["templates", "template", "machines", "machine", "requests", "request", "providers", "provider", "infrastructure", "infra"]:
            resource_map = {"template": "templates", "machine": "machines", "request": "requests", "provider": "providers", "infra": "infrastructure"}
            help_resource = resource_map.get(args.resource, args.resource)
            
            if help_resource in resource_parsers:
                resource_parsers[help_resource].print_help()
                sys.exit(0)

        # Apply global overrides BEFORE any command execution (including special cases)
        # Handle global scheduler override
        scheduler_override_active = False
        if hasattr(args, "scheduler") and args.scheduler:
            try:
                # Use DI container's ConfigurationPort for consistency
                from infrastructure.di.container import get_container
                from domain.base.ports.configuration_port import ConfigurationPort
                
                container = get_container()
                config = container.get(ConfigurationPort)
                config.override_scheduler_strategy(args.scheduler)
                scheduler_override_active = True
            except Exception as e:
                logger = get_logger(__name__)
                logger.warning("Failed to override scheduler strategy: %s", e)

        # Handle global provider override
        provider_override_active = False
        if hasattr(args, "provider") and args.provider:
            try:
                # Use DI container's ConfigurationPort for consistency
                from infrastructure.di.container import get_container
                from domain.base.ports.configuration_port import ConfigurationPort
                
                container = get_container()
                config = container.get(ConfigurationPort)
                config.override_provider_instance(args.provider)
                provider_override_active = True
            except Exception as e:
                logger = get_logger(__name__)
                logger.warning("Failed to override provider instance: %s", e)

        # Handle global AWS overrides
        if hasattr(args, "region") and args.region:
            try:
                container = get_container()
                config = container.get(ConfigurationPort)
                config.override_aws_region(args.region)
            except Exception as e:
                logger = get_logger(__name__)
                logger.warning("Failed to override region: %s", e)

        if hasattr(args, "profile") and args.profile:
            try:
                container = get_container()
                config = container.get(ConfigurationPort)
                config.override_aws_profile(args.profile)
            except Exception as e:
                logger = get_logger(__name__)
                logger.warning("Failed to override profile: %s", e)

        # Skip application initialization for init and templates generate commands only
        if args.resource == "init":
            # Execute init command directly without Application
            from interface.init_command_handler import handle_init

            result = await handle_init(args)
            sys.exit(result)

        if args.resource == "templates" and args.action == "generate":
            # Templates generate doesn't need existing config (creates templates)
            # But it DOES need scheduler/provider overrides which are now applied above
            from interface.templates_generate_handler import handle_templates_generate

            try:
                result = await handle_templates_generate(args)
                
                # Print result
                if result.get("status") == "success":
                    sys.exit(0)
                else:
                    print(f"Error: {result.get('message')}", file=sys.stderr)
                    sys.exit(1)
            except Exception as e:
                import traceback
                traceback.print_exc()
                sys.exit(1)

        # All other commands need full Application initialization
        skip_validation = False

        # Initialize application with dry-run mode if requested
        try:
            from bootstrap import Application

            app = Application(args.config, skip_validation=skip_validation)
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
                    result = await execute_command(args, app, resource_parsers)
            else:
                result = await execute_command(args, app, resource_parsers)

            # Handle different result formats from improved response formatter
            if isinstance(result, tuple) and len(result) == 2:
                # Response formatter returned (formatted_output, exit_code)
                formatted_output, exit_code = result
            else:
                # Response formatter returned formatted string only
                formatted_output = result
                exit_code = 0

            # Output the result
            if args.output:
                with open(args.output, "w") as f:
                    f.write(formatted_output)
                if not args.quiet:
                    print(f"Output written to {args.output}")
            else:
                print(formatted_output)

            # Exit with appropriate code
            if exit_code != 0:
                sys.exit(exit_code)

        except DomainException as e:
            logger.exception("Domain error: %s", e)
            
            # Use response formatter for consistent error formatting
            from cli.response_formatter import create_cli_formatter
            formatter = create_cli_formatter()
            output_format = getattr(args, "format", "json")
            error_output, exit_code = formatter.format_error(e, output_format)
            
            if not args.quiet:
                print(error_output)
            sys.exit(exit_code)
        except Exception as e:
            logger.exception("Unexpected error: %s", e)
            
            # Use response formatter for consistent error formatting
            from cli.response_formatter import create_cli_formatter
            formatter = create_cli_formatter()
            output_format = getattr(args, "format", "json")
            error_output, exit_code = formatter.format_error(e, output_format)
            
            if args.verbose:
                import traceback
                traceback.print_exc()
            if not args.quiet:
                print(error_output)
            sys.exit(exit_code)
        finally:
            # Restore original overrides if they were active
            if scheduler_override_active:
                try:
                    from infrastructure.di.container import get_container
                    from domain.base.ports.configuration_port import ConfigurationPort
                    
                    container = get_container()
                    config = container.get(ConfigurationPort)
                    config.restore_scheduler_strategy()
                except Exception as e:
                    logger = get_logger(__name__)
                    logger.warning("Failed to restore scheduler strategy: %s", e)

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(130)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
