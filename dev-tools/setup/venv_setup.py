#!/usr/bin/env python3
"""Virtual environment setup script."""

import logging
import shutil
import subprocess
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
from pathlib import Path


def main() -> int:
    """Setup virtual environment with uv or pip fallback."""
    venv_dir = Path(".venv")
    python_exe = sys.executable

    # Create venv if it doesn't exist
    if not venv_dir.exists():
        logger.info("Creating virtual environment...")
        subprocess.run([python_exe, "-m", "venv", str(venv_dir)], check=True)

    # Determine pip path
    if sys.platform == "win32":
        pip_path = venv_dir / "Scripts" / "pip"
    else:
        pip_path = venv_dir / "bin" / "pip"

    # Upgrade pip using uv or pip
    if shutil.which("uv"):
        logger.info("INFO: Using uv for virtual environment setup...")
        subprocess.run(["uv", "pip", "install", "--upgrade", "pip"], check=True)
    else:
        logger.info("INFO: Using pip for virtual environment setup...")
        subprocess.run([str(pip_path), "install", "--upgrade", "pip"], check=True)

    # Touch activate file
    if sys.platform == "win32":
        activate_file = venv_dir / "Scripts" / "activate"
    else:
        activate_file = venv_dir / "bin" / "activate"

    activate_file.touch()
    logger.info("Virtual environment setup complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
