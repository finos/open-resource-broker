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

# Auto-detect config directory based on installation type
parent_dir = os.path.dirname(current_dir)
if os.path.exists(os.path.join(parent_dir, 'pyproject.toml')):
    # Development/local install - use project config
    config_dir = os.path.join(parent_dir, 'config')
    work_dir = parent_dir
    os.environ.setdefault('HF_PROVIDER_CONFDIR', config_dir)
    os.environ.setdefault('HF_PROVIDER_WORKDIR', work_dir)
    os.environ.setdefault('DEFAULT_PROVIDER_CONFDIR', config_dir)
else:
    # System install - find install root by navigating up from site-packages
    install_root = current_dir
    while install_root != os.path.dirname(install_root):  # Stop at filesystem root
        install_root = os.path.dirname(install_root)
        if os.path.exists(os.path.join(install_root, 'config')):
            config_dir = os.path.join(install_root, 'config')
            work_dir = install_root
            os.environ.setdefault('HF_PROVIDER_CONFDIR', config_dir)
            os.environ.setdefault('HF_PROVIDER_WORKDIR', work_dir)
            os.environ.setdefault('DEFAULT_PROVIDER_CONFDIR', config_dir)
            break

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
