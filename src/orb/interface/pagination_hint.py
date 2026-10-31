"""Tell a CLI user how to continue a paged listing."""

from __future__ import annotations

import sys
from typing import Any


def print_cursor_hint(args: Any, result: Any) -> None:
    """Print ``next cursor: <token>`` on stderr for the human-readable formats.

    JSON and YAML output already carry ``next_cursor`` in the document, and
    stdout stays clean for the table, so the hint goes to stderr.
    """
    cursor = getattr(result, "next_cursor", None)
    if cursor and getattr(args, "format", None) in ("table", "list"):
        print(f"next cursor: {cursor}", file=sys.stderr)
