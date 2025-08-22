#!/usr/bin/env python3
"""CLI entry point with resource-action structure."""

import asyncio
import os
import sys

# Add project root to Python path to enable absolute imports
# This allows src/run.py to import using src.module.submodule pattern
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)  # Go up one level from src/ to project root
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Import CLI modules
try:
    # Try wheel/installed package import first
    from cli.main import main
except ImportError:
    # Fallback to development mode import
    from cli.main import main

# Import version for help text
try:
    from ._package import __version__
except ImportError:
    # Fallback for direct execution
    __version__ = "0.1.0"


def cli_main() -> None:
    """Entry point function for console scripts."""
    return asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
