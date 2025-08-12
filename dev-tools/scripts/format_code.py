#!/usr/bin/env python3
"""
Code formatting script for Open Host Factory Plugin.

Runs autoflake, autopep8, black, and isort to format Python code using the run-tool function.
"""

import logging
import subprocess
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def run_tool(tool_name, *args):
    """Run a tool using the run-tool script (same as Makefile)."""
    script_dir = Path(__file__).parent
    run_tool_script = script_dir / "run_tool.sh"

    command = [str(run_tool_script), tool_name] + list(args)

    logger.info(f"Running {tool_name}...")
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        if result.stdout:
            logger.info(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        logger.info(f"Error running {tool_name}: {e}")
        if e.stdout:
            logger.info(f"STDOUT: {e.stdout}")
        if e.stderr:
            logger.info(f"STDERR: {e.stderr}")
        return False


def main():
    """Main formatting function."""
    # Get project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent

    # Change to project root
    import os

    os.chdir(project_root)

    # Define all directories with Python files
    python_dirs = ["src", "tests", "dev-tools", "examples"]
    python_files = ["hfmock.py"]

    logger.info("Formatting Python code...")

    # Run formatters in order using run-tool
    success = True

    # autoflake - remove unused imports/variables
    if not run_tool(
        "autoflake",
        "--in-place",
        "--remove-all-unused-imports",
        "--remove-unused-variables",
        "--recursive",
        *python_dirs,
        *python_files,
    ):
        success = False

    # autopep8 - fix line length (88 to leave room for Black's decisions)
    if not run_tool(
        "autopep8",
        "--in-place",
        "--max-line-length=88",
        "--select=E501",
        "--recursive",
        *python_dirs,
        *python_files,
    ):
        success = False

    # black - code formatting (uses pyproject.toml line-length=100)
    if not run_tool("black", *python_dirs, *python_files):
        success = False

    # isort - import sorting (uses pyproject.toml config)
    if not run_tool("isort", *python_dirs, *python_files):
        success = False

    if success:
        logger.info("Formatting complete.")
        return 0
    else:
        logger.info("Some formatters failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
