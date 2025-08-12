#!/usr/bin/env python3
"""
Code formatting script for Open Host Factory Plugin.

Runs autoflake, autopep8, black, and isort to format Python code using the run-tool function.
"""

import subprocess
import sys
from pathlib import Path


def run_tool(tool_name, *args):
    """Run a tool using the run-tool script (same as Makefile)."""
    script_dir = Path(__file__).parent
    run_tool_script = script_dir / "run_tool.sh"

    command = [str(run_tool_script), tool_name] + list(args)

    print(f"Running {tool_name}...")
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error running {tool_name}: {e}")
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

    # Run formatters in order using run-tool
    success = True

    # autoflake - remove unused imports/variables
    if not run_tool("autoflake", "--in-place", "--remove-all-unused-imports",
                   "--remove-unused-variables", "--recursive", package_dir, tests_dir):
        success = False

    # autopep8 - fix line length (88 to leave room for Black's decisions)
    if not run_tool("autopep8", "--in-place", "--max-line-length=88",
                   "--select=E501", "--recursive", package_dir, tests_dir):
        success = False

    # black - code formatting (uses pyproject.toml line-length=100)
    if not run_tool("black", package_dir, tests_dir):
        success = False

    # isort - import sorting (uses pyproject.toml config)
    if not run_tool("isort", package_dir, tests_dir):
        success = False

    if success:
        print("Formatting complete.")
        return 0
    else:
        print("Some formatters failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
