#!/usr/bin/env python3
"""
Code formatting script for Open Host Factory Plugin.

Runs autoflake, autopep8, black, and isort to format Python code.
"""

import subprocess
import sys
from pathlib import Path


def run_formatter(command, description):
    """Run a formatter command and handle errors."""
    print(f"Running {description}...")
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error running {description}: {e}")
        if e.stdout:
            print(f"STDOUT: {e.stdout}")
        if e.stderr:
            print(f"STDERR: {e.stderr}")
        return False


def main():
    """Main formatting function."""
    # Get project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent

    # Change to project root
    import os
    os.chdir(project_root)

    # Define source directories
    package_dir = "src"
    tests_dir = "tests"

    print("Formatting Python code...")

    # Run formatters in order
    formatters = [
        (
            [".venv/bin/autoflake", "--in-place", "--remove-all-unused-imports",
             "--remove-unused-variables", "--recursive", package_dir, tests_dir],
            "autoflake (remove unused imports/variables)"
        ),
        (
            [".venv/bin/autopep8", "--in-place", "--max-line-length=88",
             "--select=E501", "--recursive", package_dir, tests_dir],
            "autopep8 (fix line length)"
        ),
        (
            [".venv/bin/black", package_dir, tests_dir],
            "black (code formatting)"
        ),
        (
            [".venv/bin/isort", package_dir, tests_dir],
            "isort (import sorting)"
        ),
    ]

    success = True
    for command, description in formatters:
        if not run_formatter(command, description):
            success = False

    if success:
        print("Formatting complete.")
        return 0
    else:
        print("Some formatters failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
