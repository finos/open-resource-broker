"""Add the ``scheduler`` command and its actions."""

from __future__ import annotations

from orb.cli.parsers.parent_parsers import ParentParsers


def add_scheduler_actions(subparsers, pp: ParentParsers, resource_parsers: dict) -> None:
    """Add the ``scheduler`` command and its actions."""
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
