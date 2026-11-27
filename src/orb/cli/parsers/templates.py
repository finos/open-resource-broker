"""Template actions for the ``templates`` / ``template`` commands."""

from orb.cli.parsers.parent_parsers import ParentParsers, add_multi_provider_arguments


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
        description="Show detailed information about a compute template.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    templates_show.add_argument("template_id", nargs="?", help="Template ID to show")
    templates_show.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to show"
    )

    templates_create = subparsers.add_parser(
        "create",
        help="Create template",
        description="Create a new compute template from a configuration file.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    templates_create.add_argument("--file", required=True, help="Template configuration file")
    templates_create.add_argument(
        "--validate-only", action="store_true", help="Only validate, do not create"
    )

    templates_update = subparsers.add_parser(
        "update",
        help="Update template",
        description="Update an existing compute template from a configuration file.",
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
        description="Delete a compute template by ID.",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope],
    )
    templates_delete.add_argument("template_id", nargs="?", help="Template ID to delete")
    templates_delete.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to delete"
    )

    templates_validate = subparsers.add_parser(
        "validate",
        help="Validate template",
        description="Validate one or more compute templates for correctness.",
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
        description="Refresh the cached template list from the provider.",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope],
    )

    templates_generate = subparsers.add_parser(
        "generate",
        help="Generate example templates",
        description="Generate example compute templates for the configured providers.",
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
    templates_generate.add_argument(
        "--slurm-conf",
        help="Path to slurm.conf for SLURM-aware template generation",
    )
