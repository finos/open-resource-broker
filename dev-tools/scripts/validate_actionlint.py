#!/usr/bin/env python3
"""Validate GitHub Actions workflows with actionlint."""

import logging
import subprocess
import sys
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def find_workflow_files():
    """Find all GitHub Actions workflow files."""
    workflows_dir = Path(".github/workflows")

    if not workflows_dir.exists():
        logger.error(".github/workflows directory not found")
        return []

    workflow_files = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
    return sorted(workflow_files)


def validate_workflows_with_actionlint(workflow_files):
    """Validate workflow files with actionlint."""
    logger.info(f"Running actionlint on {len(workflow_files)} workflow file(s)...")

    try:
        # Run actionlint on all workflow files
        cmd = ["actionlint"] + [str(f) for f in workflow_files]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode == 0:
            logger.info("SUCCESS: All workflows passed actionlint validation!")
            return True
        else:
            logger.error("FAILURE: Actionlint found issues:")
            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        logger.error(f"  {line}")
            if result.stderr:
                for line in result.stderr.strip().split("\n"):
                    if line.strip():
                        logger.error(f"  {line}")
            return False

    except FileNotFoundError:
        logger.error("actionlint not found. Please install actionlint.")
        return False
    except Exception as e:
        logger.error(f"Error running actionlint: {e}")
        return False


def main():
    """Main validation function."""
    workflow_files = find_workflow_files()

    if not workflow_files:
        logger.error("No workflow files found")
        return 1

    logger.info(f"Found {len(workflow_files)} workflow file(s):")
    for f in workflow_files:
        logger.info(f"  - {f}")
    logger.info("")

    if validate_workflows_with_actionlint(workflow_files):
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
