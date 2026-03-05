#!/usr/bin/env python3
"""UV package manager operations."""

import logging
import shutil
import subprocess
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
import time
from pathlib import Path


def check_uv_available() -> bool:
    """Check if uv is available."""
    return shutil.which("uv") is not None


def run_command(cmd: list[str], capture_output: bool = False) -> subprocess.CompletedProcess:
    """Run command and return result."""
    try:
        return subprocess.run(cmd, check=True, capture_output=capture_output, text=True)
    except subprocess.CalledProcessError as e:
        logger.info(f"Error running command: {' '.join(cmd)}")
        if capture_output and e.stdout:
            logger.info(f"stdout: {e.stdout}")
        if capture_output and e.stderr:
            logger.info(f"stderr: {e.stderr}")
        raise


def uv_lock() -> int:
    """Generate uv lock file for reproducible builds."""
    if not check_uv_available():
        logger.error(" uv not available. Install with: pip install uv")
        return 1

    logger.info("INFO: Generating uv.lock file...")
    try:
        run_command(["uv", "lock"])
        logger.info("SUCCESS: Lock file generated: uv.lock")
        return 0
    except subprocess.CalledProcessError:
        return 1


def uv_sync(dev: bool = False) -> int:
    """Sync environment with uv.lock file."""
    if not check_uv_available():
        logger.error(" uv not available. Install with: pip install uv")
        return 1

    if not Path("uv.lock").exists():
        logger.info("ERROR: No uv.lock file found")
        logger.info("Run 'make uv-lock' first.")
        return 1

    env_type = "development environment" if dev else "environment"
    logger.info(f"INFO: Syncing {env_type} with uv.lock...")

    try:
        if dev:
            run_command(["uv", "sync", "--all-groups"])
        else:
            run_command(["uv", "sync"])
        return 0
    except subprocess.CalledProcessError:
        return 1


def uv_check() -> int:
    """Check if uv is available and show version."""
    if check_uv_available():
        try:
            result = run_command(["uv", "--version"], capture_output=True)
            logger.info(f"SUCCESS: uv is available: {result.stdout.strip()}")
            logger.info("INFO: Performance comparison:")
            logger.info("  • uv is typically 10-100x faster than pip")
            logger.info("  • Better dependency resolution and error messages")
            logger.info("  • Use 'make dev-install' for faster development setup")
            return 0
        except subprocess.CalledProcessError:
            logger.error(" uv found but not working properly")
            return 1
    else:
        logger.error(" uv not available")
        logger.info("INFO: Install with: pip install uv")
        logger.info("INFO: Or use system package manager: brew install uv")
        return 1


def uv_benchmark() -> int:
    """Benchmark uv vs pip installation speed."""
    logger.info("INFO: Benchmarking uv vs pip installation speed...")
    logger.info("This will create temporary virtual environments for testing.")
    logger.info("")

    if not check_uv_available():
        logger.error(" uv not available for benchmarking")
        return 1

    # Clean up any existing test environments
    for venv_dir in [".venv-pip-test", ".venv-uv-test"]:
        if Path(venv_dir).exists():
            shutil.rmtree(venv_dir)

    try:
        logger.info("INFO: Testing pip installation speed...")
        start_time = time.time()
        run_command(["python", "-m", "venv", ".venv-pip-test"])
        run_command([".venv-pip-test/bin/pip", "install", "-e", ".[dev]"], capture_output=True)
        pip_time = time.time() - start_time

        logger.info("")
        logger.info("INFO: Testing uv installation speed...")
        start_time = time.time()
        run_command(["python", "-m", "venv", ".venv-uv-test"])
        run_command(
            [
                "uv",
                "pip",
                "install",
                "-e",
                ".[dev]",
                "--python",
                ".venv-uv-test/bin/python",
            ],
            capture_output=True,
        )
        uv_time = time.time() - start_time

        logger.info("")
        logger.info("Results:")
        logger.info(f"  pip: {pip_time:.2f}s")
        logger.info(f"  uv:  {uv_time:.2f}s")
        if uv_time > 0:
            speedup = pip_time / uv_time
            logger.info(f"  uv is {speedup:.1f}x faster!")

        return 0

    except subprocess.CalledProcessError:
        return 1
    finally:
        logger.info("")
        logger.info("INFO: Cleaning up test environments...")
        for venv_dir in [".venv-pip-test", ".venv-uv-test"]:
            if Path(venv_dir).exists():
                shutil.rmtree(venv_dir)
        logger.info("SUCCESS: Benchmark complete!")


def main() -> int:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="UV package manager operations")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Lock command
    subparsers.add_parser("lock", help="Generate uv.lock file for reproducible builds")

    # Sync commands
    subparsers.add_parser("sync", help="Sync environment with uv.lock file")
    subparsers.add_parser("sync-dev", help="Sync development environment with uv.lock file")

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
