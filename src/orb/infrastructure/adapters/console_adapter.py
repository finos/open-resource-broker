"""Console adapter — Rich-backed implementation of ConsolePort.

This module owns all Rich-console setup and output helpers.  The CLI layer
(orb.cli.console) delegates its print_* functions here so the dependency
flows downward: cli → infrastructure, not infrastructure → cli.
"""

from __future__ import annotations

import os
import sys
from functools import wraps

from orb.domain.base.ports.console_port import ConsolePort
from orb.infrastructure.constants import CONSOLE_SEPARATOR_WIDTH

# ---------------------------------------------------------------------------
# Rich setup (with graceful fallback to plain print when Rich is absent)
# ---------------------------------------------------------------------------

try:
    from rich.console import Console as _RichConsole

    _no_color_stdout = not sys.stdout.isatty() or "--no-color" in sys.argv
    _no_color_stderr = not sys.stderr.isatty() or "--no-color" in sys.argv
    _console = _RichConsole(
        no_color=_no_color_stdout,
        width=None if sys.stdout.isatty() else 2**31 - 1,
    )
    _error_console = _RichConsole(
        stderr=True,
        no_color=_no_color_stderr,
        width=None if sys.stderr.isatty() else 2**31 - 1,
    )
except ImportError:
    import re as _re

    class _PlainConsole:  # type: ignore[no-redef]
        def print(self, text="", **kwargs):
            print(_re.sub(r"\[.*?\]", "", str(text)))

    class _PlainErrorConsole:  # type: ignore[no-redef]
        def print(self, text="", **kwargs):
            import sys as _sys

            print(_re.sub(r"\[.*?\]", "", str(text)), file=_sys.stderr)

    _console = _PlainConsole()  # type: ignore[assignment]
    _error_console = _PlainErrorConsole()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Output guard
# ---------------------------------------------------------------------------


def _should_print() -> bool:
    """Return True when Rich console output is enabled.

    Console output is suppressed when ORB_LOG_CONSOLE_ENABLED=false.
    Defaults to True so interactive terminals always see output.
    """
    val = os.environ.get("ORB_LOG_CONSOLE_ENABLED")
    if val is not None:
        return val.lower() == "true"
    return True


def _console_output(func):
    """Decorator: skip the function body when console output is disabled."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if _should_print():
            return func(*args, **kwargs)
        return None

    return wrapper


# ---------------------------------------------------------------------------
# Module-level print helpers (used by orb.cli.console via delegation)
# ---------------------------------------------------------------------------


def get_console():
    """Return the shared stdout console instance."""
    return _console


@_console_output
def print_success(message: str) -> None:
    """Print a success message in green."""
    _console.print(f"[green]{message}[/green]")


@_console_output
def print_error(message: str) -> None:
    """Print an error message to stderr in red."""
    _error_console.print(f"[red]{message}[/red]")


@_console_output
def print_info(message: str) -> None:
    """Print an informational message in cyan."""
    _console.print(f"[cyan]{message}[/cyan]")


@_console_output
def print_warning(message: str) -> None:
    """Print a warning message in yellow."""
    _console.print(f"[yellow]{message}[/yellow]")


@_console_output
def print_command(message: str) -> None:
    """Print a command example in yellow."""
    _console.print(f"[yellow]{message}[/yellow]")


@_console_output
def print_separator(
    width: int = CONSOLE_SEPARATOR_WIDTH, char: str = "━", color: str = "green"
) -> None:
    """Print a separator line."""
    _console.print(f"[{color}]{char * width}[/{color}]")


@_console_output
def print_section(title: str, width: int = CONSOLE_SEPARATOR_WIDTH) -> None:
    """Print a section header."""
    _console.print(f"\n[cyan]{title}[/cyan]")
    _console.print(f"[cyan]{'-' * width}[/cyan]")


@_console_output
def print_newline() -> None:
    """Print an empty line."""
    _console.print()


@_console_output
def print_console(message: str) -> None:
    """Print plain text with no colour formatting."""
    _console.print(message)


def print_json(data: dict) -> None:
    """Print JSON data (always outputs, ignores LOG_CONSOLE_ENABLED)."""
    import json

    print(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# ConsolePort implementation
# ---------------------------------------------------------------------------


class RichConsoleAdapter(ConsolePort):
    """ConsolePort implementation backed by Rich (or plain-print fallback).

    All Rich console logic lives here.  orb.cli.console delegates its
    print_* helpers to the module-level functions above so the dependency
    flows cli → infrastructure, never infrastructure → cli.
    """

    def info(self, message: str) -> None:
        print_info(message)

    def success(self, message: str) -> None:
        print_success(message)

    def error(self, message: str) -> None:
        print_error(message)

    def warning(self, message: str) -> None:
        print_warning(message)

    def command(self, message: str) -> None:
        print_command(message)

    def separator(self, char: str = "-", width: int = 40, color: str = "") -> None:
        print_separator(width=width, char=char, color=color or "green")
