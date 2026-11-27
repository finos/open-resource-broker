"""Request actions for the ``requests`` / ``request`` commands."""

from orb.cli.parsers.parent_parsers import ParentParsers, add_cursor_argument
from orb.domain.request.value_objects import RequestStatus


def add_request_actions(subparsers, pp: "ParentParsers | None" = None):
    """Add request actions to a subparser."""
    if pp is None:
        pp = ParentParsers()
    requests_list = subparsers.add_parser(
        "list",
        help="List requests",
        description="List requests with filtering support. Use specific filters (--status, --template-id) or generic filters (--filter field=value).",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    requests_list.add_argument(
        "--status",
        choices=[s.value for s in RequestStatus],
        help="Filter by request status (specific filter)",
    )
    add_cursor_argument(requests_list)
    requests_list.add_argument("--template-id", help="Filter by template ID (specific filter)")
    requests_list.add_argument(
        "--request-type",
        choices=["acquire", "return"],
        help="Filter by request type (specific filter)",
    )
    requests_list.add_argument(
        "--start-time",
        help=(
            "Only requests created at or after this time (inclusive). ISO-8601: "
            "YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS[.ffffff][Z|+HH:MM]; no offset means UTC"
        ),
    )
    requests_list.add_argument(
        "--end-time",
        help=(
            "Only requests created at or before this time (inclusive). Same formats as "
            "--start-time; a date-only value is midnight UTC and so excludes that day"
        ),
    )

    requests_show = subparsers.add_parser(
        "show",
        help="Show request details",
        description="Show detailed information about one or more provisioning requests.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    requests_show.add_argument(
        "--all", action="store_true", help="Show status for all active requests"
    )
    requests_show.add_argument("request_id", nargs="?", help="Request ID to show")
    requests_show.add_argument(
        "--request-id", "-r", dest="flag_request_id", help="Request ID to show"
    )

    requests_cancel = subparsers.add_parser(
        "cancel",
        help="Cancel request",
        description="Cancel an in-flight provisioning request.",
        parents=[pp.common, pp.list, pp.write, pp.provider_scope],
    )
    requests_cancel.add_argument("request_id", nargs="?", help="Request ID to cancel")
    requests_cancel.add_argument(
        "--request-id", "-r", dest="flag_request_id", help="Request ID to cancel"
    )

    requests_confirm = subparsers.add_parser(
        "confirm-terminated",
        help="Confirm return-request machines are terminated",
        description=(
            "Record that machines of a return request are terminated, for instances "
            "the provider reports as not found. The provider is asked whether each "
            "machine is gone; confirmed machines are recorded as verified. If the "
            "provider still shows a machine, or cannot tell, the command fails; "
            "--force records machines it cannot verify as operator-attested. The "
            "operator is recorded as cli:<user>@<host>, which is advisory only."
        ),
        parents=[pp.common, pp.provider_scope],
    )
    requests_confirm.add_argument("request_id", nargs="?", help="Return request ID")
    requests_confirm.add_argument(
        "--request-id", "-r", dest="flag_request_id", help="Return request ID"
    )
    requests_confirm.add_argument(
        "--machine-id",
        "-m",
        dest="machine_ids",
        action="append",
        required=True,
        metavar="ID",
        help="Machine ID verified terminated (repeat for several)",
    )
    requests_confirm.add_argument(
        "--reason", required=True, help="Why the machines are known to be terminated"
    )
    requests_confirm.add_argument(
        "--force",
        action="store_true",
        help="Record machines the provider cannot verify as operator-attested",
    )

    requests_status = subparsers.add_parser(
        "status",
        help="Check request status",
        description="Check the current status of one or more provisioning requests.",
        parents=[pp.common, pp.list, pp.provider_scope, pp.hf_compat],
    )
    requests_status.add_argument(
        "--all", action="store_true", help="Check status of all active requests"
    )
    requests_status.add_argument("request_ids", nargs="*", help="Request IDs to check")
    requests_status.add_argument(
        "--request-id", "-r", action="append", dest="flag_request_ids", help="Request ID to check"
    )
    requests_status.add_argument(
        "--wait", action="store_true", help="Poll until the request reaches a terminal state"
    )
    requests_status.add_argument("--timeout", type=int, default=300, help="Wait timeout in seconds")

    requests_watch = subparsers.add_parser(
        "watch",
        help="Watch request status in real time",
        description="Watch a provisioning request and stream status updates until it settles.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    requests_watch.add_argument(
        "request_id", nargs="?", help="Request ID to watch (latest if omitted)"
    )
    requests_watch.add_argument(
        "--interval", type=int, default=5, help="Poll interval in seconds (default: 5)"
    )

    requests_list_returns = subparsers.add_parser(
        "list-returns",
        help="List return requests",
        description="List return requests with filtering support.",
        parents=[pp.common, pp.list, pp.provider_scope, pp.hf_compat],
    )
    add_cursor_argument(requests_list_returns)
    requests_list_returns.add_argument(
        "--status",
        help="Filter by return request status (pending, in_progress, complete, failed, cancelled, partial, timeout)",
    )
