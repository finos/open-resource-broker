#!/usr/bin/env python3
"""Quality check dispatcher for the Open Resource Broker."""

import subprocess
import sys


def parse_args(args):
    """Parse command line arguments."""
    return {
        "check_all": "all" in args,
        "fix_mode": "fix" in args,
        "files": [arg for arg in args if arg.endswith(".py") or "/" in arg],
    }


def run_whitespace_cleanup():
    """Run whitespace cleanup."""
    try:
        subprocess.run(["./dev-tools/scripts/dev_tools_runner.py", "clean-whitespace"], check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def run_ruff_format(fix_mode, files):
    """Run ruff format."""
    cmd = ["uv", "run", "ruff", "format"]
    if not fix_mode:
        cmd.append("--check")
    cmd.append("--quiet")

    if files:
        cmd.extend(files)
    else:
        cmd.append(".")

    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def run_ruff_check(fix_mode, files):
    """Run ruff check."""
    cmd = ["uv", "run", "ruff", "check"]
    if fix_mode:
        cmd.extend(["--fix", "--exit-zero"])
    cmd.append("--quiet")

    if files:
        cmd.extend(files)
    else:
        cmd.append(".")

    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def run_additional_checks():
    """Run additional quality checks."""
    try:
        subprocess.run(
            ["python3", "./dev-tools/scripts/quality_check.py", "--strict", "--all"], check=True
        )
        return True
    except subprocess.CalledProcessError:
        return False


def main():
    parsed = parse_args(sys.argv[1:])
    success = True

    # Run whitespace cleanup first if fixing
    if parsed["fix_mode"]:
        success &= run_whitespace_cleanup()

    # Run additional checks if all mode
    if parsed["check_all"]:
        success &= run_additional_checks()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
