"""Add the ``storage`` command and its actions."""

from __future__ import annotations

from orb.cli.parsers.parent_parsers import ParentParsers


def add_storage_actions(subparsers, pp: ParentParsers, resource_parsers: dict) -> None:
    """Add the ``storage`` command and its actions."""
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
