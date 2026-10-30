#!/usr/bin/env python3
"""CLI entry point with resource-action structure."""

import os

# Ensure UTF-8 mode regardless of system locale (PEP 540)
os.environ.setdefault("PYTHONUTF8", "1")

import asyncio  # noqa: E402
import sys  # noqa: E402

# Add project root to Python path to enable absolute imports
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)  # Go up one level from src/ to project root
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# Import version for help text
try:
    from ._package import __version__
except ImportError:
    # Fallback for direct execution
    __version__ = "0.1.0"


def cli_main() -> None:
    """Entry point function for console scripts."""
    from orb.cli.main import main

    return asyncio.run(main())


if __name__ == "__main__":
    cli_main()
