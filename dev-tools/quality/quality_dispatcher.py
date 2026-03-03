#!/usr/bin/env python3
"""Quality check dispatcher for the Open Resource Broker."""

import subprocess
import sys


def parse_args(args):
    """Parse command line arguments."""
    return {
        "fix_mode": "fix" in args,
        "files": [arg for arg in args if arg.endswith(".py") or "/" in arg],
    }


def run_whitespace_cleanup():
    """Run whitespace cleanup."""
    try:
        subprocess.run(["./dev-tools/quality/dev_tools_runner.py", "clean-whitespace"], check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def run_ruff_format():
    """Run ruff format on the entire project."""
    try:
        subprocess.run(["uv", "run", "ruff", "format", "."], check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def main():
    parsed = parse_args(sys.argv[1:])
    success = True

    if parsed["fix_mode"]:
        success &= run_ruff_format()
        success &= run_whitespace_cleanup()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
