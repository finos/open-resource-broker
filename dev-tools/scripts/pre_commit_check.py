#!/usr/bin/env python3
"""Pre-commit validation script - reads .pre-commit-config.yaml and executes hooks."""

import argparse
import subprocess
import sys
import time
from pathlib import Path

import yaml


# Colors
class Colors:
    """ANSI color codes for terminal output formatting."""

    """ANSI color codes for terminal output formatting."""
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    GRAY = "\033[0;37m"
    NC = "\033[0m"


def run_hook(name, command, warning_only=False, debug=False):
    """Run a single pre-commit hook."""
    # Split command for shell=False security
    cmd_args = command.split() if isinstance(command, str) else command

    if debug:
        logger.info(f"Running {name}...", end=" ", flush=True)
        start_time = time.time()
        result = subprocess.run(cmd_args, shell=False, capture_output=True, text=True)
        duration = time.time() - start_time
        exit_code = result.returncode
        output = result.stdout + result.stderr
    else:
        logger.info(f"Running {name}: ", end="", flush=True)
        start_time = time.time()

        # Start subprocess
        process = subprocess.Popen(
            cmd_args, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        # Show dots while running
        while process.poll() is None:
            logger.info(".", end="", flush=True)
            time.sleep(1)

        # Get results
        stdout, stderr = process.communicate()
        duration = time.time() - start_time
        exit_code = process.returncode
        output = stdout + stderr

        logger.info(" ", end="", flush=True)  # Space after dots

    if exit_code == 0:
        logger.info(f"{Colors.GREEN}PASS{Colors.NC} ({duration:.1f}s)")
        return True
    else:
        if warning_only:
            logger.info(f"{Colors.YELLOW}WARN{Colors.NC} ({duration:.1f}s)")
            if debug and output:
                logger.info(f"{Colors.YELLOW}  Output: {output}{Colors.NC}")
            return True  # Don't fail on warnings
        else:
            logger.info(f"{Colors.RED}FAIL{Colors.NC} ({duration:.1f}s)")
            if debug and output:
                logger.info(f"{Colors.RED}  Output: {output}{Colors.NC}")
            return False


def main():
    """Run pre-commit checks with configurable options."""
    """Run pre-commit checks with configurable options."""
    parser = argparse.ArgumentParser(description="Run pre-commit checks")
    parser.add_argument("--debug", "-d", action="store_true", help="Show debug output")
    parser.add_argument("--extended", "-e", action="store_true",
                        help="Show extended info")
    parser.add_argument(
        "--required-only",
        "-r",
        action="store_true",
        help="Run only required checks (skip warnings)",
    )
    args = parser.parse_args()

    # Check for yq
    if subprocess.run(["which", "yq"], capture_output=True).returncode != 0:
        logger.info(f"{Colors.RED}ERROR: yq not found. Install with:{Colors.NC}")
        if subprocess.run(["which", "apt"], capture_output=True).returncode == 0:
            logger.info(f"{Colors.BLUE}  Ubuntu/Debian: sudo apt install yq{Colors.NC}")
        elif subprocess.run(["which", "dnf"], capture_output=True).returncode == 0:
            logger.info(f"{Colors.BLUE}  RHEL/Fedora: sudo dnf install yq{Colors.NC}")
        elif subprocess.run(["which", "yum"], capture_output=True).returncode == 0:
            logger.info(f"{Colors.BLUE}  CentOS/RHEL: sudo yum install yq{Colors.NC}")
        elif subprocess.run(["which", "brew"], capture_output=True).returncode == 0:
            logger.info(f"{Colors.BLUE}  macOS: brew install yq{Colors.NC}")
        else:
            logger.info(
                f"{Colors.BLUE}  See: https://github.com/mikefarah/yq#install{Colors.NC}")
        return 1

    # Load pre-commit config
    config_file = Path(".pre-commit-config.yaml")
    if not config_file.exists():
        logger.info(f"{Colors.RED}ERROR: .pre-commit-config.yaml not found{Colors.NC}")
        return 1

    with open(config_file) as f:
        config = yaml.safe_load(f)

    hooks = config["repos"][0]["hooks"]

    logger.info("Running pre-commit checks (reading from .pre-commit-config.yaml)...")
    if args.debug:
        logger.info(f"{Colors.BLUE}DEBUG: Running in debug mode{Colors.NC}")
    if args.extended:
        logger.info(f"{Colors.BLUE}Found {len(hooks)} hooks to execute{Colors.NC}")

    failed = 0
    warned = 0
    total_time = time.time()

    for i, hook in enumerate(hooks):
        name = hook["name"]
        command = hook["entry"]
        warning_only = hook.get("warning_only", False)

        # Skip warning-only checks if --required-only flag is set
        if args.required_only and warning_only:
            continue

        if args.extended:
            logger.info(f"{Colors.BLUE}Hook {i+1}/{len(hooks)}: {name}{Colors.NC}")
            logger.info(f"{Colors.BLUE}  Command: {command}{Colors.NC}")

        success = run_hook(name, command, warning_only, args.debug)

        if not success:
            if warning_only:
                warned += 1
            else:
                failed += 1

    total_elapsed = time.time() - total_time

    # Summary
    logger.info(
        f"\nSummary: {len(hooks)} hooks executed in {Colors.GRAY}{total_elapsed:.2f}s{Colors.NC}"
    )
    if failed > 0:
        logger.info(f"{Colors.RED}Failed: {failed}{Colors.NC}")
    if warned > 0:
        logger.info(f"{Colors.YELLOW}Warnings: {warned}{Colors.NC}")

    passed = len(hooks) - failed - warned
    if passed > 0:
        logger.info(f"{Colors.GREEN}Passed: {passed}{Colors.NC}")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
