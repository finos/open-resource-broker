#!/usr/bin/env python3
"""Validate shell scripts with shellcheck."""

import logging
import subprocess
import sys
from pathlib import Path

try:
    import pathspec
except ImportError:
    pathspec = None

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def load_gitignore():
    """Load .gitignore patterns for filtering files."""
    if not pathspec:
        return None

    gitignore_path = Path(".gitignore")
    if not gitignore_path.exists():
        return None

    try:
        with open(gitignore_path, "r", encoding="utf-8") as f:
            return pathspec.PathSpec.from_lines("gitwildmatch", f)
    except Exception as e:
        logger.warning(f"Could not load .gitignore: {e}")
        return None


def find_shell_scripts(gitignore_spec=None):
    """Find all shell scripts, respecting .gitignore."""
    shell_files = []

    # Find .sh and .bash files
    for pattern in ["*.sh", "*.bash"]:
        for shell_file in Path(".").rglob(pattern):
            # Skip hidden directories and common exclusions
            if any(part.startswith(".") for part in shell_file.parts[:-1]):
                continue
            if any(
                part in ["__pycache__", "node_modules", ".venv", ".git"]
                for part in shell_file.parts
            ):
                continue

            # Check gitignore
            if gitignore_spec and gitignore_spec.match_file(str(shell_file)):
                continue

            shell_files.append(shell_file)

    return sorted(shell_files)


def validate_shell_script(file_path: Path):
    """Validate a single shell script with shellcheck."""
    logger.info(f"Validating {file_path}...")

    try:
        result = subprocess.run(
            ["shellcheck", "-x", str(file_path)], capture_output=True, text=True, check=False
        )

        if result.returncode == 0:
            logger.info(f"  VALID: {file_path}: No issues found")
            return True
        else:
            logger.error(f"  INVALID: {file_path}: Shellcheck issues:")
            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    logger.error(f"    {line}")
            return False

    except FileNotFoundError:
        logger.error("shellcheck not found. Please install shellcheck.")
        return False
    except Exception as e:
        logger.error(f"Error validating {file_path}: {e}")
        return False


def main():
    """Main validation function."""
    gitignore_spec = load_gitignore()
    shell_files = find_shell_scripts(gitignore_spec)

    if not shell_files:
        logger.info("No shell scripts found")
        return 0

    logger.info(f"Validating {len(shell_files)} shell script(s)...")
    logger.info("")

    all_valid = True
    for file_path in shell_files:
        if not validate_shell_script(file_path):
            all_valid = False

    logger.info("")
    if all_valid:
        logger.info(f"SUCCESS: All {len(shell_files)} shell scripts are valid!")
        return 0
    else:
        logger.error("FAILURE: Some shell scripts have issues.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
