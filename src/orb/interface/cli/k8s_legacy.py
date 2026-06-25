"""Shim that bridges the argparse-based ``orb`` CLI into the click-based
legacy k8s-HF CLI groups in ``orb.k8s_legacy``.

Routing table (first positional token after ``orb k8s-legacy``):
  admin              → orb.k8s_legacy.cli.hfadmin:run      (click group)
  utils              → orb.k8s_legacy.cli.hfutils:runserver (click command)
  events-db          → orb.k8s_legacy.cli.events_db:run    (click group)
  <anything else>    → orb.k8s_legacy.cli.hf:run           (click group)
    (covers: request-machines, get-request-status,
     request-return-machines, get-return-requests,
     get-available-templates, watch, run-cron)

The import of ``orb.k8s_legacy`` is intentionally deferred to handler
invocation time so that plain ``orb --help`` never fails if the
``k8s-legacy`` extra is not installed.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

_INSTALL_HINT = "orb k8s-legacy is not available. Install with: pip install orb-py[k8s-legacy]"

# Subcommands that route to hf.run (the main HostFactory click group).
# This set is used for the help text only; the default route is hf.run.
_HF_SUBCOMMANDS = (
    "request-machines",
    "get-request-status",
    "request-return-machines",
    "get-return-requests",
    "get-available-templates",
    "watch",
    "run-cron",
)


def add_k8s_legacy_subparser(subparsers) -> None:  # type: ignore[no-untyped-def]
    """Register the ``k8s-legacy`` subparser on *subparsers*.

    All trailing arguments are captured via ``argparse.REMAINDER`` and
    forwarded to the appropriate legacy click group at invocation time.
    """
    import argparse

    k8s_parser = subparsers.add_parser(
        "k8s-legacy",
        help="Legacy Symphony-on-Kubernetes HostFactory plugin",
        description=(
            "Legacy Symphony-on-Kubernetes HostFactory plugin.\n"
            "\n"
            "Available subcommands (HostFactory core):\n"
            "  request-machines          Request machines\n"
            "  get-request-status        Get status of requests\n"
            "  request-return-machines   Request machine returns\n"
            "  get-return-requests       Get return request status\n"
            "  get-available-templates   List available templates\n"
            "  watch                     Watch events (pods/nodes/kube-events/events/…)\n"
            "  run-cron                  Run k8s cron jobs\n"
            "\n"
            "Admin subcommands (prefix with 'admin'):\n"
            "  admin list-machines       List all machines\n"
            "  admin list-requests       List all requests\n"
            "  admin get-timings         Get request timings\n"
            "  admin replay              Replay events\n"
            "  admin …\n"
            "\n"
            "Utility server (prefix with 'utils'):\n"
            "  utils                     Run the HF utils FastAPI server\n"
            "\n"
            "Events DB tool (prefix with 'events-db'):\n"
            "  events-db transform       Normalise events database\n"
            "\n"
            "Use `orb k8s-legacy <subcommand> --help` for command-specific help.\n"
            "\n"
            "Note: requires `pip install orb-py[k8s-legacy]`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # Prevent argparse from consuming '--help' before we forward it.
        add_help=True,
    )

    # Capture everything after 'k8s-legacy' verbatim.  argparse.REMAINDER
    # preserves flags (e.g. --help) and lets click handle them.
    k8s_parser.add_argument(
        "remainder",
        nargs=argparse.REMAINDER,
        help="Subcommand and arguments forwarded to the legacy CLI",
    )


def handle_k8s_legacy(args: argparse.Namespace) -> None:
    """Dispatch ``orb k8s-legacy <remainder>`` to the correct legacy group.

    Called from ``orb.cli.main`` after argument parsing.  Never returns
    normally — always calls ``sys.exit``.
    """
    remainder: list[str] = list(getattr(args, "remainder", []) or [])

    # ── Detect bare invocation (no subcommand → show our own help) ──────────
    # When 'orb k8s-legacy' is invoked with no args, or only '--help',
    # argparse has already printed the help and exited before we get here
    # (because add_help=True above). The only case where remainder is empty
    # here is if argparse swallowed nothing and we want the legacy group help.
    if not remainder:
        # Show our argparse-level help (re-invoke with --help).
        import subprocess  # nosec B404

        subprocess.run(  # nosec B603
            [sys.executable, "-m", "orb", "k8s-legacy", "--help"],
            check=False,
        )
        sys.exit(0)

    # ── Lazy import guard ────────────────────────────────────────────────────
    # orb.k8s_legacy.__init__ has no heavy deps; guard on 'kubernetes' instead,
    # which is the canonical indicator that the k8s-legacy extra is installed.
    try:
        import kubernetes  # noqa: F401  # pyright: ignore[reportUnusedImport]
    except ImportError:
        print(_INSTALL_HINT, file=sys.stderr)
        sys.exit(2)

    # ── Route to the correct click entry point ───────────────────────────────
    first = remainder[0]

    if first == "admin":
        # orb k8s-legacy admin <verb> [opts]  →  hfadmin.run
        from orb.k8s_legacy.cli.hfadmin import run as click_entry

        click_args = remainder[1:]  # strip 'admin', pass verb + opts
    elif first == "utils":
        # orb k8s-legacy utils [opts]  →  hfutils.runserver
        # hfutils.runserver is a bare @click.command, not a group, so we
        # drop 'utils' and forward all remaining tokens directly.
        from orb.k8s_legacy.cli.hfutils import runserver as click_entry  # type: ignore[assignment]

        click_args = remainder[1:]  # strip 'utils'
    elif first == "events-db":
        # orb k8s-legacy events-db transform [opts]  →  events_db.run
        from orb.k8s_legacy.cli.events_db import run as click_entry

        click_args = remainder[1:]  # strip 'events-db', pass 'transform' + opts
    else:
        # Default: forward everything as-is to the main HF group (hf.run).
        # Covers: request-machines, get-request-status, watch, run-cron, …
        from orb.k8s_legacy.cli.hf import run as click_entry

        click_args = remainder  # keep first token (e.g. 'request-machines')

    # ── Invoke the click entry point ─────────────────────────────────────────
    # standalone_mode=False: click returns the result instead of calling
    # sys.exit internally, which lets us control the exit code cleanly.
    # We catch click.exceptions.* to forward exit codes without tracebacks.
    try:
        import click

        result = click_entry.main(  # type: ignore[union-attr]
            args=click_args,
            standalone_mode=False,
            prog_name=f"orb k8s-legacy{' ' + first if first not in _HF_SUBCOMMANDS else ''}",
        )
        # standalone_mode=False: click returns 0 on success, raises on error.
        sys.exit(0 if result is None else int(result))

    except click.exceptions.Exit as exc:
        sys.exit(exc.exit_code)

    except click.exceptions.Abort:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)

    except click.exceptions.UsageError as exc:
        # Print click's formatted error without a Python traceback.
        print(f"Error: {exc.format_message()}", file=sys.stderr)
        sys.exit(2)

    except SystemExit:
        raise

    except Exception as exc:
        # Unexpected error from the legacy code — surface it without a raw
        # traceback leaking through argparse.
        print(f"k8s-legacy error: {exc}", file=sys.stderr)
        sys.exit(1)
