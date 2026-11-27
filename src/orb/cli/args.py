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

from orb.cli.parsers.config import add_config_actions
from orb.cli.parsers.infrastructure import add_infrastructure_actions
from orb.cli.parsers.init import add_init_parser
from orb.cli.parsers.machines import add_machine_actions
from orb.cli.parsers.mcp import add_mcp_actions
from orb.cli.parsers.parent_parsers import (
    ParentParsers,
    add_cursor_argument,
    add_force_argument,
    add_multi_provider_arguments,
)
from orb.cli.parsers.providers import add_provider_actions
from orb.cli.parsers.requests import add_request_actions
from orb.cli.parsers.scheduler import add_scheduler_actions
from orb.cli.parsers.server import add_server_actions
from orb.cli.parsers.storage import add_storage_actions
from orb.cli.parsers.system import add_system_actions
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
    add_init_parser(subparsers)

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
    add_config_actions(subparsers, pp, resource_parsers)

    # Scheduler
    add_scheduler_actions(subparsers, pp, resource_parsers)

    # Storage
    add_storage_actions(subparsers, pp, resource_parsers)

    # System
    add_system_actions(subparsers, pp, resource_parsers)

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
    add_server_actions(subparsers, pp, resource_parsers)

    # MCP
    add_mcp_actions(subparsers, pp, resource_parsers)

    # k8s-legacy — optional plugin; lazy-imports orb_k8s_legacy only when invoked
    from orb.interface.cli.k8s_legacy import add_k8s_legacy_subparser

    add_k8s_legacy_subparser(subparsers)

    return parser, resource_parsers


def parse_args() -> tuple[argparse.Namespace, dict]:
    """Parse command line arguments. Thin wrapper around build_parser()."""
    parser, resource_parsers = build_parser()
    return parser.parse_args(), resource_parsers
