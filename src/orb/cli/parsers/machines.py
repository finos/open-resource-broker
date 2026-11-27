"""Machine actions for the ``machines`` / ``machine`` commands."""

from orb.cli.parsers.parent_parsers import ParentParsers, add_cursor_argument
from orb.domain.machine.machine_status import MachineStatus


def add_machine_actions(subparsers, pp: "ParentParsers | None" = None):
    """Add machine actions to a subparser."""
    if pp is None:
        pp = ParentParsers()
    machines_list = subparsers.add_parser(
        "list",
        help="List machines",
        description="List machines with filtering support. Use specific filters (--status, --request-id) or generic filters (--filter field=value).",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    machines_list.add_argument(
        "--status",
        choices=[s.value for s in MachineStatus],
        help="Filter by machine status (specific filter)",
    )
    add_cursor_argument(machines_list)
    machines_list.add_argument("--request-id", dest="request_id", help="Filter by request ID")
    machines_list.add_argument(
        "--timestamp-format",
        choices=["auto", "unix", "iso"],
        default="auto",
        help="Timestamp format: auto (scheduler default), unix (seconds), iso (ISO 8601)",
    )

    machines_show = subparsers.add_parser(
        "show",
        help="Show machine details",
        description="Show detailed information about one or more machines.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    machines_show.add_argument("machine_id", nargs="?", help="Machine ID to show")
    machines_show.add_argument(
        "--machine-id", "-m", dest="flag_machine_id", help="Machine ID to show"
    )

    machines_request = subparsers.add_parser(
        "request",
        help="Request machines",
        description="Request machines from a template and provision them via the active provider.",
        parents=[pp.common, pp.list, pp.provider_scope, pp.hf_compat],
    )
    machines_request.add_argument("template_id", nargs="?", help="Template ID to use")
    machines_request.add_argument(
        "machine_count", nargs="?", type=int, help="Number of machines to request"
    )
    machines_request.add_argument(
        "--template-id", "-t", dest="flag_template_id", help="Template ID to use"
    )
    machines_request.add_argument(
        "--count", "-c", type=int, dest="flag_machine_count", help="Number of machines to request"
    )
    machines_request.add_argument(
        "--nodes", type=str, help="SLURM node list to associate with this request"
    )
    machines_request.add_argument(
        "--wait", action="store_true", help="Wait for machines to be ready"
    )
    machines_request.add_argument(
        "--timeout", type=int, default=300, help="Wait timeout in seconds"
    )

    machines_return = subparsers.add_parser(
        "return",
        help="Return machines",
        description="Return machines to the provider, releasing the underlying compute resources.",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope, pp.hf_compat],
    )
    machines_return.add_argument(
        "--all", action="store_true", help="Return all machines for the active provider"
    )
    machines_return.add_argument("machine_ids", nargs="*", help="Machine IDs to return")
    machines_return.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to return"
    )
    machines_return.add_argument(
        "--request-id",
        "-r",
        dest="request_id",
        help=(
            "Return all machines belonging to this acquisition request ID. "
            "Mutually exclusive with machine IDs and --all."
        ),
    )
    machines_return.add_argument(
        "--wait", action="store_true", help="Wait for return request to complete"
    )
    machines_return.add_argument("--timeout", type=int, default=300, help="Wait timeout in seconds")

    machines_terminate = subparsers.add_parser(
        "terminate",
        help="Terminate (return) machines",
        description="Terminate machines and return them to the provider (alias for return).",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope],
    )
    machines_terminate.add_argument(
        "--all", action="store_true", help="Terminate all machines for the active provider"
    )
    machines_terminate.add_argument("machine_ids", nargs="*", help="Machine IDs to terminate")
    machines_terminate.add_argument(
        "--machine-id",
        "-m",
        action="append",
        dest="machine_ids_flag",
        help="Machine ID to terminate",
    )
    machines_terminate.add_argument(
        "--nodes", type=str, help="SLURM node list to terminate by node name"
    )
    machines_terminate.add_argument(
        "--wait", action="store_true", help="Wait for terminate request to complete"
    )
    machines_terminate.add_argument(
        "--timeout", type=int, default=300, help="Wait timeout in seconds"
    )

    machines_status = subparsers.add_parser(
        "status",
        help="Check machine status",
        description="Check the current status of one or more machines.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    machines_status.add_argument(
        "--all", action="store_true", help="Check status of all machines for the active provider"
    )
    machines_status.add_argument("machine_ids", nargs="*", help="Machine IDs to check")
    machines_status.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to check"
    )

    machines_stop = subparsers.add_parser(
        "stop",
        help="Stop running machines",
        description="Stop running machines without releasing them from the provider.",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope],
    )
    machines_stop.add_argument(
        "--all", action="store_true", help="Stop all running machines for the active provider"
    )
    machines_stop.add_argument("machine_ids", nargs="*", help="Machine IDs to stop")
    machines_stop.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to stop"
    )

    machines_start = subparsers.add_parser(
        "start",
        help="Start stopped machines",
        description="Start previously stopped machines for the active provider.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    machines_start.add_argument(
        "--all", action="store_true", help="Start all stopped machines for the active provider"
    )
    machines_start.add_argument("machine_ids", nargs="*", help="Machine IDs to start")
    machines_start.add_argument(
        "--machine-id", "-m", action="append", dest="machine_ids_flag", help="Machine ID to start"
    )
