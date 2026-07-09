"""Shared console helpers for CLI output — delegates to the infrastructure adapter.

All Rich setup and output logic lives in
``orb.infrastructure.adapters.console_adapter``.  This module re-exports the
public helpers so that existing ``from orb.cli.console import print_*`` call
sites keep working without change.  The dependency now flows downward:
cli → infrastructure (correct), not infrastructure → cli (violation).
"""

from orb.infrastructure.adapters.console_adapter import (
    get_console,
    print_command,
    print_console,
    print_error,
    print_info,
    print_json,
    print_newline,
    print_section,
    print_separator,
    print_success,
    print_warning,
)

__all__ = [
    "get_console",
    "print_command",
    "print_console",
    "print_error",
    "print_info",
    "print_json",
    "print_newline",
    "print_section",
    "print_separator",
    "print_success",
    "print_warning",
]
