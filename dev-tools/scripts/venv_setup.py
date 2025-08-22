#!/usr/bin/env python3
"""Virtual environment setup script."""

import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Setup virtual environment with uv or pip fallback."""
    venv_dir = Path(".venv")
    python_exe = sys.executable

    # Create venv if it doesn't exist
    if not venv_dir.exists():
        print("Creating virtual environment...")
        subprocess.run([python_exe, "-m", "venv", str(venv_dir)], check=True)

    # Determine pip path
    if sys.platform == "win32":
        pip_path = venv_dir / "Scripts" / "pip"
    else:
        pip_path = venv_dir / "bin" / "pip"

    # Upgrade pip using uv or pip
    if shutil.which("uv"):
        print("INFO: Using uv for virtual environment setup...")
        subprocess.run(["uv", "pip", "install", "--upgrade", "pip"], check=True)
    else:
        print("INFO: Using pip for virtual environment setup...")
        subprocess.run([str(pip_path), "install", "--upgrade", "pip"], check=True)

    # Touch activate file
    if sys.platform == "win32":
        activate_file = venv_dir / "Scripts" / "activate"
    else:
        activate_file = venv_dir / "bin" / "activate"

    activate_file.touch()
    print("Virtual environment setup complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
