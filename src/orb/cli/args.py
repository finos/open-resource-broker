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
from typing import cast

from orb.cli.parsers.common_args import add_provider_spec_arguments, add_provider_type_arg
from orb.cli.parsers.infrastructure import add_infrastructure_actions
from orb.cli.parsers.machines import add_machine_actions
from orb.cli.parsers.parent_parsers import (
    ParentParsers,
    add_cursor_argument,
    add_force_argument,
    add_multi_provider_arguments,
)
from orb.cli.parsers.providers import add_provider_actions
from orb.cli.parsers.requests import add_request_actions
from orb.cli.parsers.templates import add_template_actions

# The parser building blocks live in orb.cli.parsers.*; they are re-exported here
# because tests and external callers import them from orb.cli.args.  Do not drop
# a name without repointing every importer first.
__all__ = [
    "HELP_FORMATTER",
    "ParentParsers",
    "add_cursor_argument",
    "add_force_argument",
    "add_infrastructure_actions",
    "add_machine_actions",
    "add_multi_provider_arguments",
    "add_provider_actions",
    "add_request_actions",
    "add_template_actions",
    "build_parser",
    "parse_args",
]

try:
    import rich.console as _rich_console  # type: ignore[import-untyped,import-not-found]
    from rich_argparse import (  # type: ignore[import-untyped,import-not-found]
        RawDescriptionRichHelpFormatter as _RichHelpFormatter,
    )

    class _TtyAwareFormatter(_RichHelpFormatter):
        """Rich formatter that keeps description/epilog layout and produces plain text when stderr is not a TTY."""

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


class _OrbArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose sub-parsers all default to HELP_FORMATTER.

    argparse builds sub-parsers with ``type(parser)``, so using this class at the
    top level gives every nested parser the same help layout.
    """

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("formatter_class", HELP_FORMATTER)
        super().__init__(*args, **kwargs)


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

    parser = cast(
        argparse.ArgumentParser,
        _OrbArgumentParser(
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
        ),
    )

    parser.add_argument("--config", help="Configuration file path")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help="Set logging level (default: from configuration or ORB_LOG_LEVEL)",
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
        dest="resource", metavar="<command>", help="Available resources or legacy commands"
    )
    resource_parsers = {}

    # Init
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize ORB configuration",
        description="Initialize ORB configuration, optionally discovering infrastructure interactively.",
    )
    add_force_argument(init_parser)
    init_parser.add_argument("--non-interactive", action="store_true", help="Non-interactive mode")
    init_parser.add_argument(
        "--scheduler", choices=["default", "hostfactory", "slurm"], help="Scheduler type"
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
    add_provider_spec_arguments(init_parser)

    # Templates
    templates_parser = subparsers.add_parser(
        "templates",
        help="Manage compute templates",
        description="Manage compute templates — list, show, create, update, and validate them.",
    )
    resource_parsers["templates"] = templates_parser
    templates_subparsers = templates_parser.add_subparsers(
        dest="action", metavar="<action>", help="Template actions"
    )

    template_parser = subparsers.add_parser(
        "template",
        description="Manage compute templates (singular alias for templates).",
    )
    resource_parsers["template"] = template_parser
    template_subparsers = template_parser.add_subparsers(
        dest="action", metavar="<action>", help="Template actions"
    )

    add_template_actions(templates_subparsers, pp)
    add_template_actions(template_subparsers, pp)

    # Machines
    machines_parser = subparsers.add_parser(
        "machines",
        help="Manage compute instances",
        description="Manage compute instances — request, list, show, return, and control machines.",
    )
    resource_parsers["machines"] = machines_parser
    machines_subparsers = machines_parser.add_subparsers(
        dest="action", metavar="<action>", help="Machine actions"
    )

    machine_parser = subparsers.add_parser(
        "machine",
        description="Manage compute instances (singular alias for machines).",
    )
    resource_parsers["machine"] = machine_parser
    machine_subparsers = machine_parser.add_subparsers(
        dest="action", metavar="<action>", help="Machine actions"
    )

    add_machine_actions(machines_subparsers, pp)
    add_machine_actions(machine_subparsers, pp)

    # Requests
    requests_parser = subparsers.add_parser(
        "requests",
        help="Manage provisioning requests",
        description="Manage provisioning requests — list, show, cancel, and watch them.",
    )
    resource_parsers["requests"] = requests_parser
    requests_subparsers = requests_parser.add_subparsers(
        dest="action", metavar="<action>", help="Request actions"
    )

    request_parser = subparsers.add_parser(
        "request",
        description="Manage provisioning requests (singular alias for requests).",
    )
    resource_parsers["request"] = request_parser
    request_subparsers = request_parser.add_subparsers(
        dest="action", metavar="<action>", help="Request actions"
    )

    add_request_actions(requests_subparsers, pp)
    add_request_actions(request_subparsers, pp)

    # Providers
    providers_parser = subparsers.add_parser(
        "providers",
        help="Manage cloud providers",
        description="Manage cloud providers — list, add, update, remove, and inspect provider instances.",
    )
    resource_parsers["providers"] = providers_parser
    providers_subparsers = providers_parser.add_subparsers(
        dest="action", metavar="<action>", help="Provider actions"
    )

    provider_parser = subparsers.add_parser(
        "provider",
        description="Manage cloud providers (singular alias for providers).",
    )
    resource_parsers["provider"] = provider_parser
    provider_subparsers = provider_parser.add_subparsers(
        dest="action", metavar="<action>", help="Provider actions"
    )

    add_provider_actions(providers_subparsers, pp)
    add_provider_actions(provider_subparsers, pp)

    # Config
    config_parser = subparsers.add_parser(
        "config",
        help="Manage ORB configuration",
        description="Manage ORB configuration — show, get, set, validate, and reload settings.",
    )
    resource_parsers["config"] = config_parser
    config_subparsers = config_parser.add_subparsers(
        dest="action", metavar="<action>", help="Config actions", required=True
    )

    config_subparsers.add_parser(
        "show",
        help="Show configuration",
        description="Show the current ORB configuration.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    config_set = config_subparsers.add_parser(
        "set",
        help="Set configuration",
        description="Set a configuration value by key.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    config_set.add_argument("key", help="Configuration key")
    config_set.add_argument("value", help="Configuration value")
    config_set.add_argument(
        "--no-persist",
        dest="persist",
        action="store_false",
        default=True,
        help="Set the value in memory only; do not write it to the config file",
    )

    config_get = config_subparsers.add_parser(
        "get",
        help="Get configuration",
        description="Get a configuration value by key.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    config_get.add_argument("key", help="Configuration key")

    config_validate = config_subparsers.add_parser(
        "validate",
        help="Validate configuration",
        description="Validate the ORB configuration file for correctness.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    config_validate.add_argument("--file", help="Configuration file to validate")

    config_subparsers.add_parser(
        "reload",
        help="Reload provider configuration",
        description="Reload provider configuration from disk.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    # Scheduler
    scheduler_parser = subparsers.add_parser(
        "scheduler",
        help="Manage scheduler strategies",
        description="Manage scheduler strategies — list, inspect, and validate them.",
    )
    resource_parsers["scheduler"] = scheduler_parser
    scheduler_subparsers = scheduler_parser.add_subparsers(
        dest="action", metavar="<action>", help="Scheduler actions", required=True
    )

    scheduler_subparsers.add_parser(
        "list",
        help="List scheduler strategies",
        description="List the available scheduler strategies.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    scheduler_show = scheduler_subparsers.add_parser(
        "show",
        help="Show scheduler details",
        description="Show the configuration of a scheduler strategy.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    scheduler_show.add_argument("--strategy", help="Show specific scheduler strategy details")

    scheduler_validate = scheduler_subparsers.add_parser(
        "validate",
        help="Validate scheduler",
        description="Validate the configuration of a scheduler strategy.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    scheduler_validate.add_argument("--strategy", help="Validate specific scheduler strategy")

    # Storage
    storage_parser = subparsers.add_parser(
        "storage",
        help="Manage storage backends",
        description="Manage storage backends — list, inspect, validate, test, and migrate strategies.",
    )
    resource_parsers["storage"] = storage_parser
    storage_subparsers = storage_parser.add_subparsers(
        dest="action", metavar="<action>", help="Storage actions", required=True
    )

    storage_subparsers.add_parser(
        "list",
        help="List storage strategies",
        description="List the available storage strategies.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    storage_show = storage_subparsers.add_parser(
        "show",
        help="Show storage configuration",
        description="Show the configuration of the active or selected storage strategy.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    storage_show.add_argument("--strategy", help="Show specific storage strategy details")

    storage_validate = storage_subparsers.add_parser(
        "validate",
        help="Validate storage",
        description="Validate the configuration of a storage strategy.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    storage_validate.add_argument("--strategy", help="Validate specific storage strategy")

    storage_test = storage_subparsers.add_parser(
        "test",
        help="Test storage connectivity",
        description="Test connectivity to a storage strategy backend.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    storage_test.add_argument("--strategy", help="Test specific storage strategy")
    storage_test.add_argument("--timeout", type=int, default=30, help="Test timeout in seconds")

    storage_subparsers.add_parser(
        "health",
        help="Check storage health",
        description="Check the health of the active storage backend.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    storage_metrics = storage_subparsers.add_parser(
        "metrics",
        help="Show storage metrics",
        description="Show operational metrics for a storage strategy.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    storage_metrics.add_argument("--strategy", help="Show metrics for specific storage strategy")

    storage_migrate = storage_subparsers.add_parser(
        "migrate",
        help="Run SQL storage migrations (Alembic). No-op for JSON backend.",
        description="Run SQL storage migrations via Alembic (no-op for the JSON backend).",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    storage_migrate.add_argument(
        "migrate_subcommand",
        choices=["up", "down", "current", "history"],
        help="Alembic action: up (upgrade head), down (downgrade -1), current (show), history (list)",
    )

    # System
    system_parser = subparsers.add_parser(
        "system",
        help="Inspect system operations",
        description="Inspect system operations — status, health, metrics, and configuration reload.",
    )
    resource_parsers["system"] = system_parser
    system_subparsers = system_parser.add_subparsers(
        dest="action", metavar="<action>", help="System actions", required=True
    )

    system_subparsers.add_parser(
        "status",
        help="Show system status",
        description="Show the overall status of the ORB system.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    system_subparsers.add_parser(
        "health",
        help="Check system health",
        description="Check the health of the ORB system and its dependencies.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    system_subparsers.add_parser(
        "metrics",
        help="Show system metrics",
        description="Show operational metrics for the ORB system.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    system_subparsers.add_parser(
        "reload",
        help="Reload provider configuration",
        description="Reload provider configuration without restarting the system.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    # Infrastructure
    infrastructure_parser = subparsers.add_parser(
        "infrastructure",
        help="Discover and inspect infrastructure",
        description="Discover and inspect cloud infrastructure — VPCs, subnets, and security groups.",
    )
    resource_parsers["infrastructure"] = infrastructure_parser
    infrastructure_subparsers = infrastructure_parser.add_subparsers(
        dest="action", metavar="<action>", help="Infrastructure actions"
    )

    infra_parser = subparsers.add_parser(
        "infra",
        description="Discover and inspect cloud infrastructure (alias for infrastructure).",
    )
    resource_parsers["infra"] = infra_parser
    infra_subparsers = infra_parser.add_subparsers(
        dest="action", metavar="<action>", help="Infrastructure actions"
    )

    add_infrastructure_actions(infrastructure_subparsers, pp)
    add_infrastructure_actions(infra_subparsers, pp)

    # Server (process lifecycle — local daemon control)
    server_parser = subparsers.add_parser(
        "server",
        help="Control the ORB server process lifecycle",
        description="Control the ORB server process lifecycle — start, stop, status, restart, logs, and reload.",
    )
    resource_parsers["server"] = server_parser
    server_subparsers = server_parser.add_subparsers(
        dest="action", metavar="<action>", help="Server actions", required=True
    )

    def _add_server_start_args(p):
        # Intentional binding for server deployment.
        p.add_argument("--host", default=None, help="Server host (overrides config)")
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
        description="Start the ORB server, daemonising it by default.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    _add_server_start_args(server_start)

    server_stop = server_subparsers.add_parser(
        "stop",
        help="Stop the running ORB server",
        description="Stop the running ORB server gracefully.",
        parents=[pp.common, pp.list, pp.provider_scope],
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
        description="Show the ORB server process status and health.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    server_restart = server_subparsers.add_parser(
        "restart",
        help="Restart the ORB server",
        description="Restart the ORB server (stop then start).",
        parents=[pp.common, pp.list, pp.provider_scope],
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
        "logs",
        help="Tail the ORB server log file",
        description="Tail the ORB server log file.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    server_logs.add_argument("-n", "--lines", type=int, default=50, help="Lines to tail")

    server_subparsers.add_parser(
        "reload",
        help="Send SIGHUP to the running ORB server",
        description="Send SIGHUP to the running ORB server to reload its configuration.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    server_ui_export = server_subparsers.add_parser(
        "ui-export",
        help="Copy the compiled SPA bundle to a local directory for CDN / static-host serving",
        description="Copy the compiled SPA bundle to a local directory for CDN or static-host serving.",
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

    # MCP
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Operate the MCP (Model Context Protocol) interface",
        description="Operate the MCP (Model Context Protocol) interface — manage tools, validate, and serve.",
    )
    resource_parsers["mcp"] = mcp_parser
    mcp_subparsers = mcp_parser.add_subparsers(
        dest="action", metavar="<action>", help="MCP actions", required=True
    )

    mcp_subparsers.add_parser(
        "validate",
        help="Validate the MCP tool set offline (no server)",
        description="Validate the MCP tool set offline — build every catalog-derived "
        "tool and confirm each has a resolvable input schema, without starting a server.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    mcp_serve = mcp_subparsers.add_parser(
        "serve",
        help="Start MCP server",
        description="Start the MCP server to expose ORB tools to MCP clients.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    mcp_serve.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http"],
        default="stdio",
        help="Transport to serve over (default: stdio; 'streamable-http' aliases 'http')",
    )
    mcp_serve.add_argument(
        "--host", default="127.0.0.1", help="Host to bind for http transport (default: 127.0.0.1)"
    )
    mcp_serve.add_argument(
        "--port", type=int, default=8000, help="Port to bind for http transport (default: 8000)"
    )
    mcp_serve.add_argument(
        "--path", default="/mcp", help="URL path to mount for http transport (default: /mcp)"
    )

    # k8s-legacy — optional plugin; lazy-imports orb_k8s_legacy only when invoked
    from orb.interface.cli.k8s_legacy import add_k8s_legacy_subparser

    add_k8s_legacy_subparser(subparsers)

    return parser, resource_parsers


def parse_args() -> tuple[argparse.Namespace, dict]:
    """Parse command line arguments. Thin wrapper around build_parser()."""
    parser, resource_parsers = build_parser()
    return parser.parse_args(), resource_parsers
