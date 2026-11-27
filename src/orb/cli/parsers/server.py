"""Add the ``server`` process-lifecycle command and its actions."""

from __future__ import annotations

from orb.cli.parsers.parent_parsers import ParentParsers


def add_server_actions(subparsers, pp: ParentParsers, resource_parsers: dict) -> None:
    """Add the ``server`` process-lifecycle command and its actions."""
    server_parser = subparsers.add_parser(
        "server",
        help="Control the ORB server process lifecycle",
        description="Control the ORB server process lifecycle — start, stop, status, restart, logs, and reload.",
    )
    resource_parsers["server"] = server_parser
    server_subparsers = server_parser.add_subparsers(
        dest="action", metavar="<action>", help="Server actions", required=True
    )

    def _add_server_start_args(p):
        # Intentional binding for server deployment.
        p.add_argument("--host", default=None, help="Server host (overrides config)")
        p.add_argument("--port", type=int, default=None, help="Server port (overrides config)")
        p.add_argument("--workers", type=int, default=None, help="Number of workers")
        p.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload (dev)")
        p.add_argument("--server-log-level", default=None, help="Server log level")
        p.add_argument(
            "--socket-path",
            dest="socket_path",
            default=None,
            help="Unix domain socket path for IPC (alternative to --host/--port)",
        )
        p.add_argument(
            "--foreground",
            "-F",
            action="store_true",
            help="Run in the foreground instead of daemonising",
        )
        p.add_argument(
            "--api-only",
            dest="api_only",
            action="store_true",
            help="Skip embedded UI even if ui.enabled=true",
        )

    server_start = server_subparsers.add_parser(
        "start",
        help="Start the ORB server (daemonised by default)",
        description="Start the ORB server, daemonising it by default.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    _add_server_start_args(server_start)

    server_stop = server_subparsers.add_parser(
        "stop",
        help="Stop the running ORB server",
        description="Stop the running ORB server gracefully.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    server_stop.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Seconds to wait for graceful shutdown before SIGKILL (defaults to config)",
    )

    server_subparsers.add_parser(
        "status",
        help="Show ORB server status + health",
        description="Show the ORB server process status and health.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    server_restart = server_subparsers.add_parser(
        "restart",
        help="Restart the ORB server",
        description="Restart the ORB server (stop then start).",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    _add_server_start_args(server_restart)
    server_restart.add_argument(
        "--restart-timeout",
        type=int,
        default=None,
        dest="timeout",
        help="Seconds to wait for graceful shutdown before SIGKILL during stop phase",
    )

    server_logs = server_subparsers.add_parser(
        "logs",
        help="Tail the ORB server log file",
        description="Tail the ORB server log file.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    server_logs.add_argument("-n", "--lines", type=int, default=50, help="Lines to tail")

    server_subparsers.add_parser(
        "reload",
        help="Send SIGHUP to the running ORB server",
        description="Send SIGHUP to the running ORB server to reload its configuration.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )

    server_ui_export = server_subparsers.add_parser(
        "ui-export",
        help="Copy the compiled SPA bundle to a local directory for CDN / static-host serving",
        description="Copy the compiled SPA bundle to a local directory for CDN or static-host serving.",
        parents=[pp.common, pp.list, pp.provider_scope],
    )
    server_ui_export.add_argument(
        "--dest",
        required=True,
        help="Target directory to copy the SPA bundle into",
    )
    server_ui_export.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting into a non-empty or already-existing destination",
    )
