#!/usr/bin/env python3
"""Test runner script for the Open Host Factory Plugin."""
import sys
import subprocess
import argparse
import logging
from typing import List

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def run_command(cmd: List[str], description: str) -> bool:
    """Run a command and return success status."""
    logger.info(f"Running: {description}")
    logger.debug(f"Command: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        logger.info(f"PASS {description}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FAIL {description} (exit code: {e.returncode})")
        return False
    except FileNotFoundError:
        logger.error(f"FAIL {description} (command not found)")
        return False


def main():
    """Main test runner function."""
    parser = argparse.ArgumentParser(description="Run tests for Open Host Factory Plugin")
    parser.add_argument("--unit", action="store_true", help="Run unit tests only")
    parser.add_argument("--integration", action="store_true", help="Run integration tests only")
    parser.add_argument("--e2e", action="store_true", help="Run end-to-end tests only")
    parser.add_argument("--coverage", action="store_true", help="Run tests with coverage")
    parser.add_argument(
        "--html-coverage", action="store_true", help="Generate HTML coverage report"
    )
    parser.add_argument("--parallel", action="store_true", help="Run tests in parallel")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--fast", action="store_true", help="Skip slow tests")
    parser.add_argument("--markers", type=str, help="Run tests with specific markers")
    parser.add_argument("--path", type=str, help="Run tests in specific path")
    parser.add_argument("--keyword", "-k", type=str, help="Run tests matching keyword")
    parser.add_argument("--maxfail", type=int, default=5, help="Stop after N failures")
    parser.add_argument("--timeout", type=int, default=300, help="Test timeout in seconds")

    args = parser.parse_args()

    # Base pytest command
    pytest_cmd = ["python", "-m", "pytest"]

    # Add verbosity
    if args.verbose:
        pytest_cmd.append("-v")
    else:
        pytest_cmd.append("-q")

    # Add parallel execution (only if pytest-xdist is available)
    if args.parallel:
        try:
            import xdist

            pytest_cmd.extend(["-n", "auto"])
        except ImportError:
            print("Warning: pytest-xdist not installed, skipping parallel execution")

    # Add timeout (only if pytest-timeout is available)
    try:
        import pytest_timeout

        pytest_cmd.extend(["--timeout", str(args.timeout)])
    except ImportError:
        print("Warning: pytest-timeout not installed, skipping timeout option")

    # Add maxfail
    pytest_cmd.extend(["--maxfail", str(args.maxfail)])

    # Add coverage options
    if args.coverage or args.html_coverage:
        pytest_cmd.extend(
            ["--cov=src", "--cov-report=term-missing", "--cov-branch", "--no-cov-on-fail"]
        )

        if args.html_coverage:
            pytest_cmd.extend(["--cov-report=html:htmlcov"])

    # Determine test selection
    test_paths = []
    markers = []

    if args.unit:
        test_paths.append("tests/unit")
        markers.append("unit")

    if args.integration:
        test_paths.append("tests/integration")
        markers.append("integration")

    if args.e2e:
        test_paths.append("tests/e2e")
        markers.append("e2e")

    if args.fast:
        markers.append("not slow")

    if args.markers:
        markers.append(args.markers)

    # If no specific test type selected, run all
    if not test_paths:
        test_paths = ["tests/"]

    # Add test paths
    pytest_cmd.extend(test_paths)

    # Add markers
    if markers:
        pytest_cmd.extend(["-m", " and ".join(markers)])

    # Add specific path if provided
    if args.path:
        pytest_cmd = ["python", "-m", "pytest"] + [args.path]
        if args.verbose:
            pytest_cmd.append("-v")
        # Re-add markers for specific path
        if markers:
            pytest_cmd.extend(["-m", " and ".join(markers)])

    # Add keyword filter
    if args.keyword:
        pytest_cmd.extend(["-k", args.keyword])

    # Run the tests
    success = run_command(pytest_cmd, "Running Tests")

    if success:
        print(f"\nAll tests passed!")
        if args.html_coverage:
            print(f"Coverage report generated in htmlcov/index.html")
    else:
        print(f"\nSome tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
