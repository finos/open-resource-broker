"""Infrastructure actions for the ``infrastructure`` / ``infra`` commands."""

from orb.cli.parsers.parent_parsers import ParentParsers, add_multi_provider_arguments


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
