"""Add the ``system`` command and its actions."""

from __future__ import annotations

from orb.cli.parsers.parent_parsers import ParentParsers


def add_system_actions(subparsers, pp: ParentParsers, resource_parsers: dict) -> None:
    """Add the ``system`` command and its actions."""
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
