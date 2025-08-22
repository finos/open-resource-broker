#!/usr/bin/env python3
"""UV package manager operations."""

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List


def check_uv_available() -> bool:
    """Check if uv is available."""
    return shutil.which("uv") is not None


def run_command(cmd: List[str], capture_output: bool = False) -> subprocess.CompletedProcess:
    """Run command and return result."""
    try:
        return subprocess.run(cmd, check=True, capture_output=capture_output, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {' '.join(cmd)}")
        if capture_output and e.stdout:
            print(f"stdout: {e.stdout}")
        if capture_output and e.stderr:
            print(f"stderr: {e.stderr}")
        raise


def uv_lock() -> int:
    """Generate uv lock files for reproducible builds."""
    if not check_uv_available():
        print("ERROR: uv not available. Install with: pip install uv")
        return 1

    print("INFO: Generating uv lock files...")
    try:
        run_command(
            ["uv", "pip", "compile", "pyproject.toml", "--output-file", "requirements.lock"]
        )
        run_command(
            [
                "uv",
                "pip",
                "compile",
                "pyproject.toml",
                "--extra",
                "dev",
                "--output-file",
                "requirements-dev.lock",
            ]
        )
        print("SUCCESS: Lock files generated: requirements.lock, requirements-dev.lock")
        return 0
    except subprocess.CalledProcessError:
        return 1


def uv_sync(dev: bool = False) -> int:
    """Sync environment with uv lock files."""
    if not check_uv_available():
        print("ERROR: uv not available. Install with: pip install uv")
        return 1

    lock_file = "requirements-dev.lock" if dev else "requirements.lock"

    if not Path(lock_file).exists():
        print(f"ERROR: No lock file found: {lock_file}")
        print("Run 'make uv-lock' first.")
        return 1

    env_type = "development environment" if dev else "environment"
    print(f"INFO: Syncing {env_type} with uv lock file...")

    try:
        run_command(["uv", "pip", "sync", lock_file])
        return 0
    except subprocess.CalledProcessError:
        return 1


def uv_check() -> int:
    """Check if uv is available and show version."""
    if check_uv_available():
        try:
            result = run_command(["uv", "--version"], capture_output=True)
            print(f"SUCCESS: uv is available: {result.stdout.strip()}")
            print("INFO: Performance comparison:")
            print("  • uv is typically 10-100x faster than pip")
            print("  • Better dependency resolution and error messages")
            print("  • Use 'make dev-install' for faster development setup")
            return 0
        except subprocess.CalledProcessError:
            print("ERROR: uv found but not working properly")
            return 1
    else:
        print("ERROR: uv not available")
        print("INFO: Install with: pip install uv")
        print("INFO: Or use system package manager: brew install uv")
        return 1


def uv_benchmark() -> int:
    """Benchmark uv vs pip installation speed."""
    print("INFO: Benchmarking uv vs pip installation speed...")
    print("This will create temporary virtual environments for testing.")
    print("")

    if not check_uv_available():
        print("ERROR: uv not available for benchmarking")
        return 1

    # Clean up any existing test environments
    for venv_dir in [".venv-pip-test", ".venv-uv-test"]:
        if Path(venv_dir).exists():
            shutil.rmtree(venv_dir)

    try:
        print("INFO: Testing pip installation speed...")
        start_time = time.time()
        run_command(["python", "-m", "venv", ".venv-pip-test"])
        run_command([".venv-pip-test/bin/pip", "install", "-e", ".[dev]"], capture_output=True)
        pip_time = time.time() - start_time

        print("")
        print("INFO: Testing uv installation speed...")
        start_time = time.time()
        run_command(["python", "-m", "venv", ".venv-uv-test"])
        run_command(
            ["uv", "pip", "install", "-e", ".[dev]", "--python", ".venv-uv-test/bin/python"],
            capture_output=True,
        )
        uv_time = time.time() - start_time

        print("")
        print("Results:")
        print(f"  pip: {pip_time:.2f}s")
        print(f"  uv:  {uv_time:.2f}s")
        if uv_time > 0:
            speedup = pip_time / uv_time
            print(f"  uv is {speedup:.1f}x faster!")

        return 0

    except subprocess.CalledProcessError:
        return 1
    finally:
        print("")
        print("INFO: Cleaning up test environments...")
        for venv_dir in [".venv-pip-test", ".venv-uv-test"]:
            if Path(venv_dir).exists():
                shutil.rmtree(venv_dir)
        print("SUCCESS: Benchmark complete!")


def main() -> int:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="UV package manager operations")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Lock command
    subparsers.add_parser("lock", help="Generate uv lock files for reproducible builds")

    # Sync commands
    subparsers.add_parser("sync", help="Sync environment with uv lock files")
    subparsers.add_parser("sync-dev", help="Sync development environment with uv lock files")

    # Check command
    subparsers.add_parser("check", help="Check if uv is available and show version")

    # Benchmark command
    subparsers.add_parser("benchmark", help="Benchmark uv vs pip installation speed")

    args = parser.parse_args()

    if args.command == "lock":
        return uv_lock()
    elif args.command == "sync":
        return uv_sync(dev=False)
    elif args.command == "sync-dev":
        return uv_sync(dev=True)
    elif args.command == "check":
        return uv_check()
    elif args.command == "benchmark":
        return uv_benchmark()
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
