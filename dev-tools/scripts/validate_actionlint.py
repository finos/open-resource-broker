#!/usr/bin/env python3
"""Validate GitHub Actions workflows with actionlint."""

import logging
import subprocess
import sys
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def find_github_action_files():
    """Find all GitHub Actions workflow and action files."""
    github_dir = Path(".github")
    
    if not github_dir.exists():
        logger.error(".github directory not found")
        return [], []

    # Find workflow files
    workflows_dir = github_dir / "workflows"
    workflow_files = []
    if workflows_dir.exists():
        workflow_files = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))

    # Find action files
    actions_dir = github_dir / "actions"
    action_files = []
    if actions_dir.exists():
        action_files = list(actions_dir.glob("*/action.yml")) + list(actions_dir.glob("*/action.yaml"))

    return sorted(workflow_files), sorted(action_files)


def validate_files_with_actionlint(files, file_type):
    """Validate files with actionlint."""
    if not files:
        logger.info(f"No {file_type} files found")
        return True
        
    logger.info(f"Running actionlint on {len(files)} {file_type} file(s)...")

    try:
        # Run actionlint on files
        cmd = ["actionlint"] + [str(f) for f in files]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode == 0:
            logger.info(f"SUCCESS: All {file_type} files passed actionlint validation!")
            return True
        else:
            logger.error(f"FAILURE: Actionlint found issues in {file_type} files:")
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
    workflow_files, action_files = find_github_action_files()

    if not workflow_files and not action_files:
        logger.error("No GitHub Actions files found")
        return 1

    logger.info("Found GitHub Actions files:")
    if workflow_files:
        logger.info(f"  Workflows ({len(workflow_files)}):")
        for f in workflow_files:
            logger.info(f"    - {f}")
    if action_files:
        logger.info(f"  Actions ({len(action_files)}):")
        for f in action_files:
            logger.info(f"    - {f}")
    logger.info("")

    all_valid = True

    # Validate workflows only - actionlint doesn't validate action.yml files
    if workflow_files:
        if not validate_files_with_actionlint(workflow_files, "workflow"):
            all_valid = False
        logger.info("")

    # For action files, we already validated YAML syntax in validate_workflows.py
    if action_files:
        logger.info(f"INFO: Action files validated for YAML syntax only")
        logger.info("      (actionlint doesn't validate action.yml files - only workflows)")

    return 0 if all_valid else 1


if __name__ == "__main__":
    sys.exit(main())
