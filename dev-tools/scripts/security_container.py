#!/usr/bin/env python3
"""Container security scanning script."""

import logging
import subprocess
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def run_command(cmd: list[str]) -> int:
    """Run command and return exit code."""
    try:
        result = subprocess.run(cmd, check=True)
        return result.returncode
    except subprocess.CalledProcessError as e:
        return e.returncode
    except FileNotFoundError:
        logger.error("Command not found: %s", cmd[0])
        return 1


def main() -> int:
    """Run container security scans."""
    logger.info("Running container security scans...")

    # Ensure required tools are installed
    logger.info("Ensuring required tools are installed...")
    if (
        run_command(
            [
                "./dev-tools/scripts/install_dev_tools.py",
                "--tool",
                "trivy",
                "--tool",
                "hadolint",
            ]
        )
        != 0
    ):
        return 1

    # Build Docker image for security scan
    logger.info("Building Docker image for security scan...")
    project_name = "open-hostfactory-plugin"  # Could be made configurable
    if run_command(["docker", "build", "-t", f"{project_name}:security-scan", "."]) != 0:
        return 1

    # Run Trivy vulnerability scan
    logger.info("Running Trivy vulnerability scan...")
    exit_code = 0

    # SARIF output
    result = run_command(
        [
            "trivy",
            "image",
            "--format",
            "sarif",
            "--output",
            "trivy-results.sarif",
            f"{project_name}:security-scan",
        ]
    )
    if result != 0:
        exit_code = result

    # JSON output
    result = run_command(
        [
            "trivy",
            "image",
            "--format",
            "json",
            "--output",
            "trivy-results.json",
            f"{project_name}:security-scan",
        ]
    )
    if result != 0:
        exit_code = result

    # Run Hadolint Dockerfile scan
    logger.info("Running Hadolint Dockerfile scan...")
    try:
        with open("hadolint-results.sarif", "w") as f:
            subprocess.run(["hadolint", "Dockerfile", "--format", "sarif"], stdout=f, check=True)
    except subprocess.CalledProcessError:
        logger.info("Dockerfile issues found")
        # Don't fail the whole process for hadolint issues

    if exit_code == 0:
        logger.info("Container security scans completed successfully!")
    else:
        logger.info("Some security scans found issues")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
