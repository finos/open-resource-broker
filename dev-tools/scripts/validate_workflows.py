#!/usr/bin/env python3
"""Validate GitHub Actions workflow YAML files."""

import logging
import sys
from pathlib import Path

import yaml

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def validate_workflow_syntax(file_path: Path):
    """Validate YAML syntax of a workflow file."""
    try:
        with open(file_path, "r") as f:
            content = f.read()
            yaml.safe_load(content)
        return True, None
    except yaml.YAMLError as e:
        return False, f"YAML syntax error: {e}"
    except Exception as e:
        return False, f"File error: {e}"


def validate_workflow_structure(file_path: Path):
    """Validate GitHub Actions workflow structure."""
    try:
        with open(file_path, "r") as f:
            workflow = yaml.safe_load(f)

        issues = []

        # Check required top-level keys
        if not isinstance(workflow, dict):
            issues.append("Workflow must be a YAML object")
            return issues

        if "name" not in workflow:
            issues.append("Missing required 'name' field")

        # Check for 'on' field (can be parsed as True due to YAML boolean conversion)
        if "on" not in workflow and True not in workflow:
            issues.append("Missing required 'on' field")

        if "jobs" not in workflow:
            issues.append("Missing required 'jobs' field")

        # Check for common malformed patterns we encountered
        if "jobs" in workflow:
            for job_name, job in workflow["jobs"].items():
                if not isinstance(job, dict):
                    continue

                if "steps" in job:
                    steps = job["steps"]
                    if isinstance(steps, list):
                        for i, step in enumerate(steps):
                            if not isinstance(step, dict):
                                continue

                            # Check for malformed step structure (the main issue we had)
                            step_str = str(step)
                            if step_str.count("uses:") > 1:
                                issues.append(
                                    f"Job '{job_name}' step {i+1} has duplicate 'uses' fields"
                                )

                            # Check for steps that have name but no action (less strict)
                            if "name" in step and len(step) == 1:
                                issues.append(
                                    f"Job '{job_name}' step {i+1} ('{step['name']}') has no action"
                                )

        return issues

    except Exception as e:
        return [f"Structure validation error: {e}"]


def validate_workflow(file_path: Path):
    """Validate a single workflow file comprehensively."""
    logger.info(f"Validating {file_path.name}...")

    # First check YAML syntax
    syntax_valid, syntax_error = validate_workflow_syntax(file_path)
    if not syntax_valid:
        logger.error(f"  INVALID: {file_path.name}: {syntax_error}")
        return False

    # Then check workflow structure
    structure_issues = validate_workflow_structure(file_path)
    if structure_issues:
        logger.error(f"  INVALID: {file_path.name}: Structure issues:")
        for issue in structure_issues:
            logger.error(f"     - {issue}")
        return False

    logger.info(f"  VALID: {file_path.name}: Valid")
    return True


def main():
    """Main validation function."""
    workflows_dir = Path(".github/workflows")

    if not workflows_dir.exists():
        logger.error(".github/workflows directory not found")
        sys.exit(1)

    workflow_files = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))

    if not workflow_files:
        logger.error("No workflow files found")
        sys.exit(1)

    logger.info(f"Validating {len(workflow_files)} workflow files...")
    logger.info("")

    all_valid = True

    for workflow_file in sorted(workflow_files):
        if not validate_workflow(workflow_file):
            all_valid = False

    logger.info("")
    if all_valid:
        logger.info(f"SUCCESS: All {len(workflow_files)} workflow files are valid!")
        sys.exit(0)
    else:
        logger.error("FAILED: Found invalid workflow files")
        logger.error("Fix the issues above before committing.")
        sys.exit(1)


if __name__ == "__main__":
    main()
