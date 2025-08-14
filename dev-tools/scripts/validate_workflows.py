#!/usr/bin/env python3
"""Validate GitHub Actions workflow YAML files."""

import sys
from pathlib import Path

import yaml


def validate_workflow(file_path: Path):
    """Validate a single workflow file."""
    try:
        with open(file_path, "r") as f:
            yaml.safe_load(f)
        logger.info(f"VALID: {file_path.name}")
        return True
    except yaml.YAMLError as e:
        logger.info(f"INVALID: {file_path.name} - {e}")
        return False
    except Exception as e:
        logger.error(f"{file_path.name} - {e}")
        return False


def main():
    """Main validation function."""
    workflows_dir = Path(".github/workflows")

    if not workflows_dir.exists():
        logger.error(f".github/workflows directory not found")
        sys.exit(1)

    workflow_files = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))

    if not workflow_files:
        logger.error(f"No workflow files found")
        sys.exit(1)

    all_valid = True

    for workflow_file in sorted(workflow_files):
        if not validate_workflow(workflow_file):
            all_valid = False

    if all_valid:
        logger.info(f"All {len(workflow_files)} workflow files are valid")
        sys.exit(0)
    else:
        logger.info("FAILED: Found invalid workflow files")
        sys.exit(1)


if __name__ == "__main__":
    main()
