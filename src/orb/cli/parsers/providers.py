"""Provider actions for the ``providers`` / ``provider`` commands."""

from orb.cli.parsers.common_args import add_provider_spec_arguments
from orb.cli.parsers.parent_parsers import ParentParsers


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
        description="Show detailed configuration for a provider instance.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_show.add_argument("provider_name", nargs="?", help="Provider name to show")

    subparsers.add_parser(
        "health",
        help="Check provider health",
        description="Check the health of the active or selected provider.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    providers_add = subparsers.add_parser(
        "add",
        help="Add new provider",
        description="Add a new provider instance to the configuration.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    add_provider_spec_arguments(providers_add)
    providers_add.add_argument("--name", help="Provider instance name")
    providers_add.add_argument("--discover", action="store_true", help="Discover infrastructure")

    providers_remove = subparsers.add_parser(
        "remove",
        help="Remove provider",
        description="Remove a provider instance from the configuration.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_remove.add_argument("provider_name", help="Provider instance name to remove")

    providers_update = subparsers.add_parser(
        "update",
        help="Update provider configuration",
        description="Update the configuration of an existing provider instance.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_update.add_argument("provider_name", help="Provider instance name")
    add_provider_spec_arguments(providers_update)

    providers_set_default = subparsers.add_parser(
        "set-default",
        help="Set default provider",
        description="Set the default provider instance used when none is specified.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_set_default.add_argument("provider_name", help="Provider name to set as default")

    providers_get = subparsers.add_parser(
        "get",
        help="Get provider details by name",
        description="Get details for a provider instance by name.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_get.add_argument("name", help="Provider name")

    subparsers.add_parser(
        "get-default",
        help="Show default provider",
        description="Show the current default provider instance.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    providers_select = subparsers.add_parser(
        "select",
        help="Select provider instance",
        description="Select the active provider instance for the current session.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_select.add_argument("provider_name", help="Provider name to select")

    providers_exec = subparsers.add_parser(
        "exec",
        help="Execute a provider maintenance operation",
        description=(
            "Execute an operator-safe maintenance operation against a provider instance. "
            "Use --list to show the operations each provider instance supports."
        ),
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_exec.add_argument(
        "operation",
        nargs="?",
        help="Operation to execute (for example cleanup-orphaned-resources)",
    )
    providers_exec.add_argument(
        "--list",
        dest="list_operations",
        action="store_true",
        help="List the operations the selected provider instance (or every instance) supports",
    )
    providers_exec.add_argument(
        "--param",
        dest="param",
        action="append",
        metavar="KEY=VALUE",
        help="Operation parameter; repeat for several (values are read as JSON when possible)",
    )
    providers_exec.add_argument(
        "--params", "--args", dest="params", help="Operation parameters (JSON format)"
    )

    providers_metrics = subparsers.add_parser(
        "metrics",
        help="Show provider metrics",
        description="Show operational metrics for a provider instance.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    providers_metrics.add_argument(
        "--timeframe", default="1h", help="Metrics timeframe (e.g., 1h, 24h, 7d)"
    )
