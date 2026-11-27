"""Add the ``init`` command."""

from __future__ import annotations

from orb.cli.parsers.common_args import (
    add_provider_spec_arguments,
    add_provider_type_arg,
)
from orb.cli.parsers.parent_parsers import add_force_argument


def add_init_parser(subparsers) -> None:
    """Add the ``init`` command."""
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
