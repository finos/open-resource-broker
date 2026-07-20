"""
CLI argument parsing module.

Handles all argument parser construction including global arguments,
resource-specific actions, and the top-level parse_args function.
"""

import argparse
import os
import sys

# Optional: Rich formatting for help text
import sys as _sys

from orb.cli.parsers.common_args import add_provider_type_arg
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.value_objects import RequestStatus
from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry

try:
    import rich.console as _rich_console  # type: ignore[import-untyped,import-not-found]
    from rich_argparse import (  # type: ignore[import-untyped,import-not-found]
        RichHelpFormatter as _RichHelpFormatter,
    )

    class _TtyAwareFormatter(_RichHelpFormatter):
        """RichHelpFormatter that produces plain text when stderr is not a TTY."""

        @property
        def console(self) -> _rich_console.Console:
            if self._console is None:
                is_tty = _sys.stderr.isatty() and "--no-color" not in _sys.argv
                self._console = _rich_console.Console(
                    stderr=True,
                    force_terminal=is_tty,
                    color_system="auto" if is_tty else None,
                )
            return self._console

    HELP_FORMATTER = _TtyAwareFormatter
except ImportError:
    HELP_FORMATTER = argparse.RawDescriptionHelpFormatter


# ---------------------------------------------------------------------------
# Parent parsers grouped by intent (open-resource-broker CLI schema refactor)
# ---------------------------------------------------------------------------
#
# Every sub-parser composes the parent parsers relevant to its intent via the
# argparse ``parents=[...]`` mechanism instead of calling a single
# ``add_global_arguments`` boilerplate helper.  The upside is structural:
#
#   * A flag belongs to exactly one intent group, so its meaning is unambiguous.
#   * argparse raises ``ArgumentError`` at *build* time if a sub-parser composes
#     two parents that both declare the same option string — the duplicate is
#     caught by ``build_parser()`` rather than surfacing as a parse-time crash.
#
# Intent groups:
#   common_parser          — cross-cutting flags valid on every command
#   list_parser            — read/list-shaped commands (pagination + output)
#   write_parser           — mutating commands (--force / --yes confirmation)
#   provider_scope_parser  — commands that select/filter a provider
#   hf_compat_parser       — HostFactory-invoked commands needing -f/-d
#
# The intent parsers are intentionally FLAT (they do not inherit one another).
# Each sub-parser composes ``common_parser`` explicitly alongside the intent
# parsers it needs, e.g. ``parents=[common_parser, list_parser,
# provider_scope_parser]``.  If the intent parsers themselves re-inherited
# ``common_parser``, a command needing two of them (e.g. list + provider-scope)
# would add ``--verbose`` twice and argparse would raise ``ArgumentError`` at
# build time.  Keeping them flat preserves the build-time duplicate-detection
# guarantee (a genuine collision between two composed parents still raises)
# without the spurious diamond conflict.


class ParentParsers:
    """Container for the intent-grouped parent parsers.

    Built once per ``build_parser()`` call and threaded through the
    ``add_*_actions`` helpers so every sub-parser composes the same shared
    parent instances via ``parents=[...]``.
    """

    def __init__(self) -> None:
        # common — cross-cutting flags on every command
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--verbose", action="store_true", help="Verbose output")
        common.add_argument("--quiet", action="store_true", help="Suppress output")
        common.add_argument("--no-color", action="store_true", help="Disable colored output")
        common.add_argument("--dry-run", action="store_true", help="Preview without executing")

        # list — pagination + output shaping for read/list commands
        list_ = argparse.ArgumentParser(add_help=False)
        list_.add_argument(
            "--format",
            choices=["json", "yaml", "table", "list"],
            default="json",
            help="Output format",
        )
        list_.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of results to return (handler-specific default applies when omitted)",
        )
        list_.add_argument("--offset", type=int, default=0, help="Number of results to skip")
        list_.add_argument(
            "--filter",
            action="append",
            help="Generic filter using snake_case field names: field=value, field~value, field=~regex. Can be combined with specific filters. Use multiple times for AND logic.",
        )

        # write — confirmation flags for mutating/destructive commands
        write = argparse.ArgumentParser(add_help=False)
        write.add_argument("--force", action="store_true", help="Force without confirmation")
        write.add_argument("--yes", "-y", action="store_true", help="Assume yes to all prompts")

        # provider-scope — provider selection/filter flags
        provider_scope = argparse.ArgumentParser(add_help=False)
        provider_scope.add_argument(
            "--provider-name",
            dest="provider_name",
            metavar="NAME",
            help="Restrict to a specific provider instance by exact name (e.g. aws-prod)",
        )
        add_provider_type_arg(provider_scope)
        provider_scope.add_argument(
            "--scheduler", choices=["default", "hostfactory"], help="Override scheduler strategy"
        )

        # hf-compat — HostFactory -f/-d with distinct dests (hf_file/hf_data),
        # applied only to HF-invoked commands so they never collide with the
        # root parser's -f/--file (dest 'file') or subcommand-local --file.
        hf_compat = argparse.ArgumentParser(add_help=False)
        hf_compat.add_argument(
            "-f",
            dest="hf_file",
            metavar="FILE",
            help="Input JSON file path (HostFactory compatibility)",
        )
        hf_compat.add_argument(
            "-d",
            dest="hf_data",
            metavar="DATA",
            help="Input JSON data string (HostFactory compatibility)",
        )

        self.common = common
        self.list = list_
        self.write = write
        self.provider_scope = provider_scope
        self.hf_compat = hf_compat


def add_multi_provider_arguments(parser):
    """Add multi-provider arguments."""
    parser.add_argument("--all-providers", action="store_true", help="Apply to all providers")


def add_force_argument(parser):
    """Add --force argument for destructive operations (init only)."""
    parser.add_argument("--force", action="store_true", help="Force without confirmation")


def add_machine_actions(subparsers, pp: "ParentParsers | None" = None):
    """Add machine actions to a subparser."""
    if pp is None:
        pp = ParentParsers()
    machines_list = subparsers.add_parser(
        "list",
        help="List machines",
        description="List machines with filtering support. Use specific filters (--status, --request-id) or generic filters (--filter field=value).",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
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

    machines_show = subparsers.add_parser(
        "show",
        help="Show machine details",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    machines_show.add_argument("machine_id", nargs="?", help="Machine ID to show")
    machines_show.add_argument(
        "--machine-id", "-m", dest="flag_machine_id", help="Machine ID to show"
    )

    machines_request = subparsers.add_parser(
        "request",
        help="Request machines",
        parents=[pp.common, pp.list, pp.provider_scope, pp.hf_compat],
    )
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

    machines_return = subparsers.add_parser(
        "return",
        help="Return machines",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope, pp.hf_compat],
    )
    machines_return.add_argument(
        "--all", action="store_true", help="Return all machines for the active provider"
    )
    machines_return.add_argument("machine_ids", nargs="*", help="Machine IDs to return")
    machines_return.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to return"
    )
    machines_return.add_argument(
        "--request-id",
        "-r",
        dest="request_id",
        help=(
            "Return all machines belonging to this acquisition request ID. "
            "Mutually exclusive with machine IDs and --all."
        ),
    )
    machines_return.add_argument(
        "--wait", action="store_true", help="Wait for return request to complete"
    )
    machines_return.add_argument("--timeout", type=int, default=300, help="Wait timeout in seconds")

    machines_terminate = subparsers.add_parser(
        "terminate",
        help="Terminate (return) machines",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope],
    )
    machines_terminate.add_argument(
        "--all", action="store_true", help="Terminate all machines for the active provider"
    )
    machines_terminate.add_argument("machine_ids", nargs="*", help="Machine IDs to terminate")
    machines_terminate.add_argument(
        "--machine-id",
        "-m",
        action="append",
        dest="machine_ids_flag",
        help="Machine ID to terminate",
    )
    machines_terminate.add_argument(
        "--wait", action="store_true", help="Wait for terminate request to complete"
    )
    machines_terminate.add_argument(
        "--timeout", type=int, default=300, help="Wait timeout in seconds"
    )

    machines_status = subparsers.add_parser(
        "status",
        help="Check machine status",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    machines_status.add_argument(
        "--all", action="store_true", help="Check status of all machines for the active provider"
    )
    machines_status.add_argument("machine_ids", nargs="*", help="Machine IDs to check")
    machines_status.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to check"
    )

    machines_stop = subparsers.add_parser(
        "stop",
        help="Stop running machines",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope],
    )
    machines_stop.add_argument(
        "--all", action="store_true", help="Stop all running machines for the active provider"
    )
    machines_stop.add_argument("machine_ids", nargs="*", help="Machine IDs to stop")
    machines_stop.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to stop"
    )

    machines_start = subparsers.add_parser(
        "start",
        help="Start stopped machines",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    machines_start.add_argument(
        "--all", action="store_true", help="Start all stopped machines for the active provider"
    )
    machines_start.add_argument("machine_ids", nargs="*", help="Machine IDs to start")
    machines_start.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to start"
    )


def add_request_actions(subparsers, pp: "ParentParsers | None" = None):
    """Add request actions to a subparser."""
    if pp is None:
        pp = ParentParsers()
    requests_list = subparsers.add_parser(
        "list",
        help="List requests",
        description="List requests with filtering support. Use specific filters (--status, --template-id) or generic filters (--filter field=value).",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
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

    requests_show = subparsers.add_parser(
        "show",
        help="Show request details",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    requests_show.add_argument(
        "--all", action="store_true", help="Show status for all active requests"
    )
    requests_show.add_argument("request_id", nargs="?", help="Request ID to show")
    requests_show.add_argument(
        "--request-id", "-r", dest="flag_request_id", help="Request ID to show"
    )

    requests_cancel = subparsers.add_parser(
        "cancel",
        help="Cancel request",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope],
    )
    requests_cancel.add_argument("request_id", nargs="?", help="Request ID to cancel")
    requests_cancel.add_argument(
        "--request-id", "-r", dest="flag_request_id", help="Request ID to cancel"
    )

    requests_status = subparsers.add_parser(
        "status",
        help="Check request status",
        parents=[pp.common, pp.list, pp.provider_scope, pp.hf_compat],
    )
    requests_status.add_argument(
        "--all", action="store_true", help="Check status of all active requests"
    )
    requests_status.add_argument("request_ids", nargs="*", help="Request IDs to check")
    requests_status.add_argument(
        "--request-id", "-r", action="append", dest="flag_request_ids", help="Request ID to check"
    )

    requests_watch = subparsers.add_parser(
        "watch",
        help="Watch request status in real time",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    requests_watch.add_argument(
        "request_id", nargs="?", help="Request ID to watch (latest if omitted)"
    )
    requests_watch.add_argument(
        "--interval", type=int, default=5, help="Poll interval in seconds (default: 5)"
    )

    requests_list_returns = subparsers.add_parser(
        "list-returns",
        help="List return requests",
        parents=[pp.common, pp.list, pp.provider_scope, pp.hf_compat],
    )
    requests_list_returns.add_argument("--status", help="Filter by return request status")


def add_infrastructure_actions(subparsers, pp: "ParentParsers | None" = None):
    """Add infrastructure actions to a subparser."""
    if pp is None:
        pp = ParentParsers()
    infra_discover = subparsers.add_parser(
        "discover",
        help="Scan AWS to find available infrastructure (VPCs, subnets, security groups)",
        description="Discover available infrastructure in your AWS account.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
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
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    add_multi_provider_arguments(infra_show)

    subparsers.add_parser(
        "validate",
        help="Verify configured infrastructure still exists in AWS",
        description="Check if the infrastructure configured in ORB still exists in your AWS account.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )


def add_provider_actions(subparsers, pp: "ParentParsers | None" = None):
    """Add provider actions to a subparser."""
    if pp is None:
        pp = ParentParsers()
    subparsers.add_parser(
        "list",
        help="List providers",
        description="List providers with filtering support.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_show = subparsers.add_parser(
        "show",
        help="Show provider details",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_show.add_argument("provider_name", nargs="?", help="Provider name to show")

    subparsers.add_parser(
        "health",
        help="Check provider health",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    providers_add = subparsers.add_parser(
        "add", help="Add new provider", parents=[pp.common, pp.list, pp.provider_scope]
    )
    for _spec in CLISpecRegistry.all().values():
        _spec.add_arguments(providers_add)
    providers_add.add_argument("--name", help="Provider instance name")
    providers_add.add_argument("--discover", action="store_true", help="Discover infrastructure")

    providers_remove = subparsers.add_parser(
        "remove", help="Remove provider", parents=[pp.common, pp.list, pp.provider_scope]
    )
    providers_remove.add_argument("provider_name", help="Provider instance name to remove")

    providers_update = subparsers.add_parser(
        "update",
        help="Update provider configuration",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_update.add_argument("provider_name", help="Provider instance name")
    for _spec in CLISpecRegistry.all().values():
        _spec.add_arguments(providers_update)

    providers_set_default = subparsers.add_parser(
        "set-default",
        help="Set default provider",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_set_default.add_argument("provider_name", help="Provider name to set as default")

    providers_get = subparsers.add_parser(
        "get", help="Get provider details by name", parents=[pp.common, pp.list, pp.provider_scope]
    )
    providers_get.add_argument("name", help="Provider name")

    subparsers.add_parser(
        "get-default", help="Show default provider", parents=[pp.common, pp.list, pp.provider_scope]
    )

    providers_select = subparsers.add_parser(
        "select", help="Select provider instance", parents=[pp.common, pp.list, pp.provider_scope]
    )
    providers_select.add_argument("provider_name", help="Provider name to select")

    providers_exec = subparsers.add_parser(
        "exec", help="Execute provider command", parents=[pp.common, pp.list, pp.provider_scope]
    )
    providers_exec.add_argument("operation", help="Operation to execute")
    providers_exec.add_argument(
        "--params", "--args", dest="params", help="Operation parameters (JSON format)"
    )

    providers_metrics = subparsers.add_parser(
        "metrics", help="Show provider metrics", parents=[pp.common, pp.list, pp.provider_scope]
    )
    providers_metrics.add_argument(
        "--timeframe", default="1h", help="Metrics timeframe (e.g., 1h, 24h, 7d)"
    )


def add_template_actions(subparsers, pp: "ParentParsers | None" = None):
    """Add template actions to a subparser."""
    if pp is None:
        pp = ParentParsers()
    templates_list = subparsers.add_parser(
        "list",
        help="List templates",
        description="List templates with filtering support.",
        parents=[pp.common, pp.list, pp.provider_scope, pp.hf_compat],
    )
    templates_list.add_argument("--provider-api", help="Filter by provider API type")

    templates_show = subparsers.add_parser(
        "show",
        help="Show template details",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    templates_show.add_argument("template_id", nargs="?", help="Template ID to show")
    templates_show.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to show"
    )

    templates_create = subparsers.add_parser(
        "create",
        help="Create template",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    templates_create.add_argument("--file", required=True, help="Template configuration file")
    templates_create.add_argument(
        "--validate-only", action="store_true", help="Only validate, do not create"
    )

    templates_update = subparsers.add_parser(
        "update",
        help="Update template",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    templates_update.add_argument("template_id", nargs="?", help="Template ID to update")
    templates_update.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to update"
    )
    templates_update.add_argument(
        "--file", required=True, help="Updated template configuration file"
    )

    templates_delete = subparsers.add_parser(
        "delete",
        help="Delete template",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope],
    )
    templates_delete.add_argument("template_id", nargs="?", help="Template ID to delete")
    templates_delete.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to delete"
    )

    templates_validate = subparsers.add_parser(
        "validate",
        help="Validate template",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    templates_validate.add_argument("--all", action="store_true", help="Validate all templates")
    templates_validate.add_argument("template_id", nargs="?", help="Template ID to validate")
    templates_validate.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to validate"
    )
    templates_validate.add_argument("--file", help="Template file to validate (pre-import)")

    subparsers.add_parser(
        "refresh",
        help="Refresh template cache",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope],
    )

    templates_generate = subparsers.add_parser(
        "generate",
        help="Generate example templates",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope],
    )
    add_multi_provider_arguments(templates_generate)
    templates_generate.add_argument(
        "--provider-api", help="Provider API type (EC2Fleet, SpotFleet, ASG, RunInstances)"
    )
    templates_generate.add_argument(
        "--provider-specific",
        action="store_true",
        help="Generate templates with hardcoded infrastructure",
    )


def build_parser() -> tuple[argparse.ArgumentParser, dict]:
    """Build the argument parser with resource-action structure.

    Returns:
        tuple: (parser, resource_parsers_dict)
    """
    # Ensure CLI specs are registered before iterating the registry.  This is
    # a lightweight bootstrap (no full provider initialisation) so it is safe
    # to call before any application context exists.
    from orb.providers.registration import register_all_provider_cli_specs

    register_all_provider_cli_specs()

    from orb._package import DESCRIPTION, DOCS_URL

    parser = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description=DESCRIPTION,
        formatter_class=HELP_FORMATTER,
        epilog=f"""
Examples:
  %(prog)s templates list                              # List all templates
  %(prog)s templates list --provider-name aws-prod    # Use specific provider instance
  %(prog)s templates generate --all-providers         # Generate for all providers
  %(prog)s machines request template-id 5             # Request 5 machines
  %(prog)s machines list --filter "machine_types~t3"  # Filter machines by type
  %(prog)s requests status req-123                    # Check request status
  %(prog)s providers health --provider-name aws-prod  # Check provider health
  %(prog)s machines return --all --provider-type k8s  # Return only k8s machines

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

    # Intent-grouped parent parsers, built once and shared across all
    # sub-parsers via parents=[...]. Any duplicate option string introduced by
    # composing two parents raises argparse.ArgumentError here at build time.
    pp = ParentParsers()

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

    add_template_actions(templates_subparsers, pp)
    add_template_actions(template_subparsers, pp)

    # Machines
    machines_parser = subparsers.add_parser("machines", help="Compute instances")
    resource_parsers["machines"] = machines_parser
    machines_subparsers = machines_parser.add_subparsers(dest="action", help="Machine actions")

    machine_parser = subparsers.add_parser("machine")
    resource_parsers["machine"] = machine_parser
    machine_subparsers = machine_parser.add_subparsers(dest="action", help="Machine actions")

    add_machine_actions(machines_subparsers, pp)
    add_machine_actions(machine_subparsers, pp)

    # Requests
    requests_parser = subparsers.add_parser("requests", help="Provisioning requests")
    resource_parsers["requests"] = requests_parser
    requests_subparsers = requests_parser.add_subparsers(dest="action", help="Request actions")

    request_parser = subparsers.add_parser("request")
    resource_parsers["request"] = request_parser
    request_subparsers = request_parser.add_subparsers(dest="action", help="Request actions")

    add_request_actions(requests_subparsers, pp)
    add_request_actions(request_subparsers, pp)

    # System
    system_parser = subparsers.add_parser("system", help="System operations")
    resource_parsers["system"] = system_parser
    system_subparsers = system_parser.add_subparsers(
        dest="action", help="System actions", required=True
    )

    system_subparsers.add_parser(
        "status", help="Show system status", parents=[pp.common, pp.list, pp.provider_scope]
    )

    system_subparsers.add_parser(
        "health", help="Check system health", parents=[pp.common, pp.list, pp.provider_scope]
    )
    system_subparsers.add_parser(
        "metrics", help="Show system metrics", parents=[pp.common, pp.list, pp.provider_scope]
    )

    system_subparsers.add_parser(
        "reload",
        help="Reload provider configuration",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    # Server (process lifecycle — local daemon control)
    server_parser = subparsers.add_parser(
        "server",
        help="ORB server process lifecycle (start/stop/status/restart/logs/reload)",
    )
    resource_parsers["server"] = server_parser
    server_subparsers = server_parser.add_subparsers(
        dest="action", help="Server actions", required=True
    )

    def _add_server_start_args(p):
        # Intentional binding for server deployment.
        p.add_argument("--host", default=None, help="Server host (overrides config)")  # nosec B104
        p.add_argument("--port", type=int, default=None, help="Server port (overrides config)")
        p.add_argument("--workers", type=int, default=None, help="Number of workers")
        p.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload (dev)")
        p.add_argument("--server-log-level", default=None, help="Server log level")
        p.add_argument(
            "--socket-path",
            dest="socket_path",
            default=None,
            help="Unix domain socket path for IPC (alternative to --host/--port)",
        )
        p.add_argument(
            "--foreground",
            "-F",
            action="store_true",
            help="Run in the foreground instead of daemonising",
        )
        p.add_argument(
            "--api-only",
            dest="api_only",
            action="store_true",
            help="Skip embedded UI even if ui.enabled=true",
        )

    server_start = server_subparsers.add_parser(
        "start",
        help="Start the ORB server (daemonised by default)",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    _add_server_start_args(server_start)

    server_stop = server_subparsers.add_parser(
        "stop", help="Stop the running ORB server", parents=[pp.common, pp.list, pp.provider_scope]
    )
    server_stop.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Seconds to wait for graceful shutdown before SIGKILL (defaults to config)",
    )

    server_subparsers.add_parser(
        "status",
        help="Show ORB server status + health",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    server_restart = server_subparsers.add_parser(
        "restart", help="Restart the ORB server", parents=[pp.common, pp.list, pp.provider_scope]
    )
    _add_server_start_args(server_restart)
    server_restart.add_argument(
        "--restart-timeout",
        type=int,
        default=None,
        dest="timeout",
        help="Seconds to wait for graceful shutdown before SIGKILL during stop phase",
    )

    server_logs = server_subparsers.add_parser(
        "logs", help="Tail the ORB server log file", parents=[pp.common, pp.list, pp.provider_scope]
    )
    server_logs.add_argument("-n", "--lines", type=int, default=50, help="Lines to tail")

    server_subparsers.add_parser(
        "reload",
        help="Send SIGHUP to the running ORB server",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    server_ui_export = server_subparsers.add_parser(
        "ui-export",
        help="Copy the compiled SPA bundle to a local directory for CDN / static-host serving",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    server_ui_export.add_argument(
        "--dest",
        required=True,
        help="Target directory to copy the SPA bundle into",
    )
    server_ui_export.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting into a non-empty or already-existing destination",
    )

    # Infrastructure
    infrastructure_parser = subparsers.add_parser("infrastructure", help="Infrastructure discovery")
    resource_parsers["infrastructure"] = infrastructure_parser
    infrastructure_subparsers = infrastructure_parser.add_subparsers(
        dest="action", help="Infrastructure actions"
    )

    infra_parser = subparsers.add_parser("infra")
    resource_parsers["infra"] = infra_parser
    infra_subparsers = infra_parser.add_subparsers(dest="action", help="Infrastructure actions")

    add_infrastructure_actions(infrastructure_subparsers, pp)
    add_infrastructure_actions(infra_subparsers, pp)

    # Config
    config_parser = subparsers.add_parser("config", help="Configuration")
    resource_parsers["config"] = config_parser
    config_subparsers = config_parser.add_subparsers(
        dest="action", help="Config actions", required=True
    )

    config_subparsers.add_parser(
        "show", help="Show configuration", parents=[pp.common, pp.list, pp.provider_scope]
    )

    config_set = config_subparsers.add_parser(
        "set", help="Set configuration", parents=[pp.common, pp.list, pp.provider_scope]
    )
    config_set.add_argument("key", help="Configuration key")
    config_set.add_argument("value", help="Configuration value")

    config_get = config_subparsers.add_parser(
        "get", help="Get configuration", parents=[pp.common, pp.list, pp.provider_scope]
    )
    config_get.add_argument("key", help="Configuration key")

    config_validate = config_subparsers.add_parser(
        "validate", help="Validate configuration", parents=[pp.common, pp.list, pp.provider_scope]
    )
    config_validate.add_argument("--file", help="Configuration file to validate")

    config_subparsers.add_parser(
        "reload",
        help="Reload provider configuration",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    # Providers
    providers_parser = subparsers.add_parser("providers", help="Cloud providers")
    resource_parsers["providers"] = providers_parser
    providers_subparsers = providers_parser.add_subparsers(dest="action", help="Provider actions")

    provider_parser = subparsers.add_parser("provider")
    resource_parsers["provider"] = provider_parser
    provider_subparsers = provider_parser.add_subparsers(dest="action", help="Provider actions")

    add_provider_actions(providers_subparsers, pp)
    add_provider_actions(provider_subparsers, pp)

    # Storage
    storage_parser = subparsers.add_parser("storage", help="Storage")
    resource_parsers["storage"] = storage_parser
    storage_subparsers = storage_parser.add_subparsers(
        dest="action", help="Storage actions", required=True
    )

    storage_subparsers.add_parser(
        "list", help="List storage strategies", parents=[pp.common, pp.list, pp.provider_scope]
    )

    storage_show = storage_subparsers.add_parser(
        "show", help="Show storage configuration", parents=[pp.common, pp.list, pp.provider_scope]
    )
    storage_show.add_argument("--strategy", help="Show specific storage strategy details")

    storage_validate = storage_subparsers.add_parser(
        "validate", help="Validate storage", parents=[pp.common, pp.list, pp.provider_scope]
    )
    storage_validate.add_argument("--strategy", help="Validate specific storage strategy")

    storage_test = storage_subparsers.add_parser(
        "test", help="Test storage connectivity", parents=[pp.common, pp.list, pp.provider_scope]
    )
    storage_test.add_argument("--strategy", help="Test specific storage strategy")
    storage_test.add_argument("--timeout", type=int, default=30, help="Test timeout in seconds")

    storage_subparsers.add_parser(
        "health", help="Check storage health", parents=[pp.common, pp.list, pp.provider_scope]
    )
    storage_metrics = storage_subparsers.add_parser(
        "metrics", help="Show storage metrics", parents=[pp.common, pp.list, pp.provider_scope]
    )
    storage_metrics.add_argument("--strategy", help="Show metrics for specific storage strategy")

    storage_migrate = storage_subparsers.add_parser(
        "migrate",
        help="Run SQL storage migrations (Alembic). No-op for JSON backend.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    storage_migrate.add_argument(
        "migrate_subcommand",
        choices=["up", "down", "current", "history"],
        help="Alembic action: up (upgrade head), down (downgrade -1), current (show), history (list)",
    )

    # Scheduler
    scheduler_parser = subparsers.add_parser("scheduler", help="Scheduler")
    resource_parsers["scheduler"] = scheduler_parser
    scheduler_subparsers = scheduler_parser.add_subparsers(
        dest="action", help="Scheduler actions", required=True
    )

    scheduler_subparsers.add_parser(
        "list", help="List scheduler strategies", parents=[pp.common, pp.list, pp.provider_scope]
    )

    scheduler_show = scheduler_subparsers.add_parser(
        "show", help="Show scheduler details", parents=[pp.common, pp.list, pp.provider_scope]
    )
    scheduler_show.add_argument("--strategy", help="Show specific scheduler strategy details")

    scheduler_validate = scheduler_subparsers.add_parser(
        "validate", help="Validate scheduler", parents=[pp.common, pp.list, pp.provider_scope]
    )
    scheduler_validate.add_argument("--strategy", help="Validate specific scheduler strategy")

    # MCP
    mcp_parser = subparsers.add_parser("mcp", help="MCP (Model Context Protocol) operations")
    resource_parsers["mcp"] = mcp_parser
    mcp_subparsers = mcp_parser.add_subparsers(dest="action", help="MCP actions", required=True)

    mcp_tools = mcp_subparsers.add_parser("tools", help="MCP tools management")
    mcp_tools_sub = mcp_tools.add_subparsers(dest="tools_action", required=True)

    mcp_tools_list = mcp_tools_sub.add_parser(
        "list", help="List MCP tools", parents=[pp.common, pp.list, pp.provider_scope]
    )
    mcp_tools_list.add_argument(
        "--type", choices=["command", "query"], help="Filter tools by handler type"
    )

    mcp_tools_call = mcp_tools_sub.add_parser(
        "call", help="Call MCP tool", parents=[pp.common, pp.list, pp.provider_scope]
    )
    mcp_tools_call.add_argument("tool_name", help="Name of tool to call")
    mcp_tools_call.add_argument("--args", help="Tool arguments as JSON string")
    mcp_tools_call.add_argument("--file", help="Tool arguments from JSON file")

    mcp_tools_info = mcp_tools_sub.add_parser(
        "info", help="Show MCP tool details", parents=[pp.common, pp.list, pp.provider_scope]
    )
    mcp_tools_info.add_argument("tool_name", help="Name of tool to get info for")

    mcp_validate = mcp_subparsers.add_parser(
        "validate", help="Validate MCP", parents=[pp.common, pp.list, pp.provider_scope]
    )
    mcp_validate.add_argument("--config", help="MCP configuration file to validate")

    mcp_serve = mcp_subparsers.add_parser(
        "serve", help="Start MCP server", parents=[pp.common, pp.list, pp.provider_scope]
    )
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

    # k8s-legacy — optional plugin; lazy-imports orb_k8s_legacy only when invoked
    from orb.interface.cli.k8s_legacy import add_k8s_legacy_subparser

    add_k8s_legacy_subparser(subparsers)

    # Init
    init_parser = subparsers.add_parser("init", help="Initialize ORB configuration")
    add_force_argument(init_parser)
    init_parser.add_argument("--non-interactive", action="store_true", help="Non-interactive mode")
    init_parser.add_argument(
        "--scheduler", choices=["default", "hostfactory"], help="Scheduler type"
    )
    add_provider_type_arg(
        init_parser,
        default="aws",
        extra_help="Provider type to initialise.",
    )
    init_parser.add_argument("--config-dir", help="Custom configuration directory")
    init_parser.add_argument(
        "--scripts-dir",
        dest="scripts_dir",
        help="Directory for ORB scripts (default: derived from config dir or ORB_SCRIPTS_DIR)",
    )
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
    # Inject per-provider CLI flags (e.g. --aws-profile, --aws-region) so that
    # _get_default_config can use spec.extract_config() in non-interactive mode.
    for _spec in CLISpecRegistry.all().values():
        _spec.add_arguments(init_parser)

    return parser, resource_parsers


def parse_args() -> tuple[argparse.Namespace, dict]:
    """Parse command line arguments. Thin wrapper around build_parser()."""
    parser, resource_parsers = build_parser()
    return parser.parse_args(), resource_parsers
