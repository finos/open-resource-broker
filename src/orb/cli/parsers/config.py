"""Add the ``config`` command and its actions."""

from __future__ import annotations

from orb.cli.parsers.parent_parsers import ParentParsers


def add_config_actions(subparsers, pp: ParentParsers, resource_parsers: dict) -> None:
    """Add the ``config`` command and its actions."""
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
