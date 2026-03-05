"""Shared Rich console for CLI output with graceful fallback."""

import os
import sys
from functools import wraps

from infrastructure.constants import CONSOLE_SEPARATOR_WIDTH

# Try to import Rich, fallback to plain print if not available
try:
    from rich.console import Console

    RICH_AVAILABLE = True
    _console = Console()
    _error_console = Console(stderr=True)
except ImportError:
    RICH_AVAILABLE = False

    # Create plain print wrappers
    class PlainConsole:
        def print(self, text="", **kwargs):
            # Strip Rich markup
            import re

            clean = re.sub(r"\[.*?\]", "", str(text))
            print(clean)

    class PlainErrorConsole:
        def print(self, text="", **kwargs):
            import re

            clean = re.sub(r"\[.*?\]", "", str(text))
            print(clean, file=sys.stderr)

    _console = PlainConsole()
    _error_console = PlainErrorConsole()


def _should_print() -> bool:
    """Check if console output is enabled via ORB_LOG_CONSOLE_ENABLED."""
    val = os.environ.get("ORB_LOG_CONSOLE_ENABLED")
    if val is not None:
        return val.lower() == "true"
    return True


def _console_output(func):
    """Decorator to check if console output is enabled."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if _should_print():
            return func(*args, **kwargs)

    return wrapper


def get_console():
    """Get the shared console instance."""
    return _console


@_console_output
def print_success(message: str):
    """Print success message."""
    _console.print(f"[green]{message}[/green]")


@_console_output
def print_error(message: str):
    """Print error message to stderr."""
    _error_console.print(f"[red]{message}[/red]")


@_console_output
def print_info(message: str):
    """Print info message."""
    _console.print(f"[cyan]{message}[/cyan]")


@_console_output
def print_warning(message: str):
    """Print warning message."""
    _console.print(f"[yellow]{message}[/yellow]")


@_console_output
def print_command(message: str):
    """Print command example."""
    _console.print(f"[yellow]{message}[/yellow]")


@_console_output
def print_separator(width: int = CONSOLE_SEPARATOR_WIDTH, char: str = "━", color: str = "green"):
    """Print separator line."""
    _console.print(f"[{color}]{char * width}[/{color}]")


@_console_output
def print_section(title: str, width: int = CONSOLE_SEPARATOR_WIDTH):
    """Print section header."""
    _console.print(f"\n[cyan]{title}[/cyan]")
    _console.print(f"[cyan]{'-' * width}[/cyan]")


@_console_output
def print_newline():
    """Print an empty line."""
    _console.print()


def print_json(data: dict):
    """Print JSON data (always outputs, ignores LOG_CONSOLE_ENABLED)."""
    import json

    print(json.dumps(data, indent=2))
