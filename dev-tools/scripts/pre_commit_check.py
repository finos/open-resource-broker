#!/usr/bin/env python3
"""Pre-commit validation script - reads .pre-commit-config.yaml and executes hooks."""

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

# Colors
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'

def show_progress(stop_event):
    """Show progress dots while command runs."""
    while not stop_event.is_set():
        print(".", end="", flush=True)
        time.sleep(0.5)

def run_hook(name, command, warning_only=False, debug=False):
    """Run a single pre-commit hook."""
    print(f"Running {name}... ", end="", flush=True)

    if debug:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        exit_code = result.returncode
        output = result.stdout + result.stderr
    else:
        # Show progress dots
        stop_event = threading.Event()
        progress_thread = threading.Thread(target=show_progress, args=(stop_event,))
        progress_thread.start()

        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        exit_code = result.returncode

        stop_event.set()
        progress_thread.join()

    if exit_code == 0:
        print(f"{Colors.GREEN}PASS{Colors.NC}")
        return True
    else:
        if warning_only:
            print(f"{Colors.YELLOW}WARN{Colors.NC}")
            if debug:
                print(f"{Colors.YELLOW}  Output: {output}{Colors.NC}")
            else:
                print(f"  Command: {command} (warning only)")
            return True  # Don't fail on warnings
        else:
            print(f"{Colors.RED}FAIL{Colors.NC}")
            if debug:
                print(f"{Colors.RED}  Output: {output}{Colors.NC}")
            else:
                print(f"  Command: {command}")
            return False

def main():
    parser = argparse.ArgumentParser(description="Run pre-commit checks")
    parser.add_argument("--debug", "-d", action="store_true", help="Show debug output")
    parser.add_argument("--extended", "-e", action="store_true", help="Show extended info")
    args = parser.parse_args()

    # Check for yq
    if subprocess.run(["which", "yq"], capture_output=True).returncode != 0:
        print(f"{Colors.RED}ERROR: yq not found. Install with:{Colors.NC}")
        if subprocess.run(["which", "apt"], capture_output=True).returncode == 0:
            print(f"{Colors.BLUE}  Ubuntu/Debian: sudo apt install yq{Colors.NC}")
        elif subprocess.run(["which", "dnf"], capture_output=True).returncode == 0:
            print(f"{Colors.BLUE}  RHEL/Fedora: sudo dnf install yq{Colors.NC}")
        elif subprocess.run(["which", "yum"], capture_output=True).returncode == 0:
            print(f"{Colors.BLUE}  CentOS/RHEL: sudo yum install yq{Colors.NC}")
        elif subprocess.run(["which", "brew"], capture_output=True).returncode == 0:
            print(f"{Colors.BLUE}  macOS: brew install yq{Colors.NC}")
        else:
            print(f"{Colors.BLUE}  See: https://github.com/mikefarah/yq#install{Colors.NC}")
        return 1

    # Load pre-commit config
    config_file = Path(".pre-commit-config.yaml")
    if not config_file.exists():
        print(f"{Colors.RED}ERROR: .pre-commit-config.yaml not found{Colors.NC}")
        return 1

    with open(config_file) as f:
        config = yaml.safe_load(f)

    hooks = config["repos"][0]["hooks"]

    print("Running pre-commit checks (reading from .pre-commit-config.yaml)...")
    if args.debug:
        print(f"{Colors.BLUE}DEBUG: Running in debug mode{Colors.NC}")
    if args.extended:
        print(f"{Colors.BLUE}Found {len(hooks)} hooks to execute{Colors.NC}")

    failed = 0
    warned = 0

    for i, hook in enumerate(hooks):
        name = hook["name"]
        command = hook["entry"]
        warning_only = hook.get("warning_only", False)

        if args.extended:
            print(f"{Colors.BLUE}Hook {i+1}/{len(hooks)}: {name}{Colors.NC}")
            print(f"{Colors.BLUE}  Command: {command}{Colors.NC}")

        success = run_hook(name, command, warning_only, args.debug)

        if not success:
            if warning_only:
                warned += 1
            else:
                failed += 1

    # Summary
    print(f"\nSummary: {len(hooks)} hooks executed")
    if failed > 0:
        print(f"{Colors.RED}Failed: {failed}{Colors.NC}")
    if warned > 0:
        print(f"{Colors.YELLOW}Warnings: {warned}{Colors.NC}")

    passed = len(hooks) - failed - warned
    if passed > 0:
        print(f"{Colors.GREEN}Passed: {passed}{Colors.NC}")

    return 1 if failed > 0 else 0

if __name__ == "__main__":
    sys.exit(main())
