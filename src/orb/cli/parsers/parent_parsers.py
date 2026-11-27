"""Intent-grouped parent parsers shared by every CLI sub-parser."""

import argparse

from orb.cli.parsers.common_args import add_provider_type_arg

# ---------------------------------------------------------------------------
# Parent parsers grouped by intent (open-resource-broker CLI schema refactor)
# ---------------------------------------------------------------------------
#
# Every sub-parser composes the parent parsers relevant to its intent via the
# argparse ``parents=[...]`` mechanism instead of calling a single
# ``add_global_arguments`` boilerplate helper.  The upside is structural:
#
#   * A flag belongs to exactly one intent group, so its meaning is unambiguous.
#   * argparse raises ``ArgumentError`` at *build* time if a sub-parser composes
#     two parents that both declare the same option string — the duplicate is
#     caught by ``build_parser()`` rather than surfacing as a parse-time crash.
#
# Intent groups:
#   common_parser          — cross-cutting flags valid on every command
#   list_parser            — read/list-shaped commands (pagination + output)
#   write_parser           — mutating commands (--force / --yes confirmation)
#   provider_scope_parser  — commands that select/filter a provider
#   hf_compat_parser       — HostFactory-invoked commands needing -f/-d
#
# The intent parsers are intentionally FLAT (they do not inherit one another).
# Each sub-parser composes ``common_parser`` explicitly alongside the intent
# parsers it needs, e.g. ``parents=[common_parser, list_parser,
# provider_scope_parser]``.  If the intent parsers themselves re-inherited
# ``common_parser``, a command needing two of them (e.g. list + provider-scope)
# would add ``--verbose`` twice and argparse would raise ``ArgumentError`` at
# build time.  Keeping them flat preserves the build-time duplicate-detection
# guarantee (a genuine collision between two composed parents still raises)
# without the spurious diamond conflict.


def add_cursor_argument(parser: argparse.ArgumentParser) -> None:
    """Add ``--cursor`` to a command that pages its results with a keyset cursor."""
    parser.add_argument(
        "--cursor",
        default=None,
        help="Continue after the page that returned this cursor (the next_cursor of the previous page); overrides --offset",
    )


class ParentParsers:
    """Container for the intent-grouped parent parsers.

    Built once per ``build_parser()`` call and threaded through the
    ``add_*_actions`` helpers so every sub-parser composes the same shared
    parent instances via ``parents=[...]``.
    """

    def __init__(self) -> None:
        """Build the common, list, write, provider-scope and hf-compat parents."""
        # common — cross-cutting flags on every command
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--verbose", action="store_true", help="Verbose output")
        common.add_argument("--quiet", action="store_true", help="Suppress output")
        common.add_argument("--no-color", action="store_true", help="Disable colored output")
        common.add_argument("--dry-run", action="store_true", help="Preview without executing")

        # list — pagination + output shaping for read/list commands
        list_ = argparse.ArgumentParser(add_help=False)
        list_.add_argument(
            "--format",
            choices=["json", "yaml", "table", "list"],
            default="json",
            help="Output format",
        )
        list_.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of results to return (handler-specific default applies when omitted)",
        )
        list_.add_argument("--offset", type=int, default=0, help="Number of results to skip")
        list_.add_argument(
            "--filter",
            action="append",
            help="Generic filter using snake_case field names: field=value, field~value, field=~regex. Can be combined with specific filters. Use multiple times for AND logic.",
        )

        # write — confirmation flags for mutating/destructive commands
        write = argparse.ArgumentParser(add_help=False)
        write.add_argument("--force", action="store_true", help="Force without confirmation")
        write.add_argument("--yes", "-y", action="store_true", help="Assume yes to all prompts")

        # provider-scope — provider selection/filter flags
        provider_scope = argparse.ArgumentParser(add_help=False)
        provider_scope.add_argument(
            "--provider-name",
            dest="provider_name",
            metavar="NAME",
            help="Restrict to a specific provider instance by exact name (e.g. aws-prod)",
        )
        add_provider_type_arg(provider_scope)
        provider_scope.add_argument(
            "--scheduler",
            choices=["default", "hostfactory", "slurm"],
            help="Override scheduler strategy",
        )

        # hf-compat — HostFactory -f/-d with distinct dests (hf_file/hf_data),
        # applied only to HF-invoked commands so they never collide with the
        # root parser's -f/--file (dest 'file') or subcommand-local --file.
        hf_compat = argparse.ArgumentParser(add_help=False)
        hf_compat.add_argument(
            "-f",
            dest="hf_file",
            metavar="FILE",
            help="Input JSON file path (HostFactory compatibility)",
        )
        hf_compat.add_argument(
            "-d",
            dest="hf_data",
            metavar="DATA",
            help="Input JSON data string (HostFactory compatibility)",
        )

        self.common = common
        self.list = list_
        self.write = write
        self.provider_scope = provider_scope
        self.hf_compat = hf_compat


def add_multi_provider_arguments(parser):
    """Add multi-provider arguments."""
    parser.add_argument("--all-providers", action="store_true", help="Apply to all providers")


def add_force_argument(parser):
    """Add --force argument for destructive operations (init only)."""
    parser.add_argument("--force", action="store_true", help="Force without confirmation")
