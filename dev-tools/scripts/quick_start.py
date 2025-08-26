#!/usr/bin/env python3
"""
Quick Start Setup Script

Complete setup for new developers:
1. Install required system tools (yq, uv, docker)
2. Generate pyproject.toml from template
3. Install Python development dependencies
4. Verify setup works
5. Show next steps

Usage:
    python dev-tools/scripts/quick_start.py [--tools-only] [--verify-only]

Options:
    --tools-only    Only install system tools
    --verify-only   Only run verification checks
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def run_command(cmd, description="", check=True):
    """Run a command and handle errors."""
    logger.info(f"Running: {description or ' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=check, capture_output=True, text=True)
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed: {description}")
        if e.stderr:
            logger.error(f"Error: {e.stderr}")
        return False
    except FileNotFoundError:
        logger.error(f"Command not found: {cmd[0]}")
        return False


def install_system_tools():
    """Install required system tools."""
    logger.info("=== Installing Required System Tools ===")

    script_path = Path(__file__).parent / "install_dev_tools.py"
    success = run_command([str(script_path), "--required-only"], "Installing yq, uv, docker")

    if not success:
        logger.error("Failed to install required system tools")
        return False

    logger.info("System tools installed successfully")
    return True


def generate_pyproject():
    """Generate pyproject.toml from template."""
    logger.info("=== Generating pyproject.toml ===")

    success = run_command(["make", "generate-pyproject"], "Generating pyproject.toml from template")

    if not success:
        logger.error("Failed to generate pyproject.toml")
        return False

    logger.info("pyproject.toml generated successfully")
    return True


def install_python_deps():
    """Install Python development dependencies."""
    logger.info("=== Installing Python Dependencies ===")

    success = run_command(["make", "dev-install"], "Installing Python development dependencies")

    if not success:
        logger.error("Failed to install Python dependencies")
        return False

    logger.info("Python dependencies installed successfully")
    return True


def verify_setup():
    """Verify the setup works correctly."""
    logger.info("=== Verifying Setup ===")

    checks = [
        (["uv", "--version"], "UV installation"),
        (["yq", "--version"], "YQ installation"),
        (["python3", "--version"], "Python installation"),
        (["docker", "--version"], "Docker installation"),
    ]

    all_passed = True
    for cmd, description in checks:
        if run_command(cmd, f"Checking {description}", check=False):
            logger.info(f"PASS: {description}")
        else:
            logger.warning(f"FAIL: {description}")
            all_passed = False

    # Test basic functionality
    logger.info("Testing basic project functionality...")
    if run_command(["make", "test-quick"], "Running quick tests", check=False):
        logger.info("PASS: Quick tests")
    else:
        logger.warning("FAIL: Quick tests (this may be normal for new setup)")

    return all_passed


def show_next_steps():
    """Show helpful next steps for developers."""
    logger.info("=== Quick Start Completed ===")
    logger.info("")
    logger.info("Next steps:")
    logger.info("  make test              - Run full test suite")
    logger.info("  make docs-serve        - Start documentation server")
    logger.info("  make lint              - Run code quality checks")
    logger.info("  make dev               - Quick development workflow")
    logger.info("  make help              - Show all available commands")
    logger.info("")
    logger.info("Optional: Install additional security tools:")
    logger.info("  make install-dev-tools - Install all development tools")


def main():
    """Main quick start function."""
    parser = argparse.ArgumentParser(description="Quick start setup for new developers")
    parser.add_argument("--tools-only", action="store_true", help="Only install system tools")
    parser.add_argument("--verify-only", action="store_true", help="Only run verification checks")

    args = parser.parse_args()

    logger.info("Open Host Factory Plugin - Quick Start Setup")
    logger.info("=" * 50)

    success = True

    if args.verify_only:
        success = verify_setup()
    elif args.tools_only:
        success = install_system_tools()
    else:
        # Full setup
        steps = [
            ("Installing system tools", install_system_tools),
            ("Generating pyproject.toml", generate_pyproject),
            ("Installing Python dependencies", install_python_deps),
            ("Verifying setup", verify_setup),
        ]

        for step_name, step_func in steps:
            logger.info(f"Step: {step_name}")
            if not step_func():
                logger.error(f"Failed at step: {step_name}")
                success = False
                break

        if success:
            show_next_steps()

    if success:
        logger.info("Quick start completed successfully!")
        return 0
    else:
        logger.error("Quick start failed. Please check the errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
