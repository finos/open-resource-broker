#!/usr/bin/env python3
"""
SARIF file validation script.

Validates SARIF files for compliance with the SARIF 2.1.0 specification
and GitHub Security tab requirements.
"""
import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


class SarifValidator:
    """SARIF file validator."""

    def __init__(self):
        """Initialize SARIF validator."""
        self.errors = []
        self.warnings = []

    def validate_sarif_structure(self, sarif_data: Dict) -> bool:
        """Validate basic SARIF structure."""
        required_fields = ["version", "runs"]

        for field in required_fields:
            if field not in sarif_data:
                self.errors.append(f"Missing required field: {field}")
                return False

        # Check version
        if sarif_data.get("version") != "2.1.0":
            self.warnings.append(
                f"SARIF version {sarif_data.get('version')} may not be fully supported. Recommended: 2.1.0"
            )

        # Check runs array
        if not isinstance(sarif_data.get("runs"), list):
            self.errors.append("'runs' must be an array")
            return False

        if len(sarif_data["runs"]) == 0:
            self.warnings.append("No runs found in SARIF file")

        return True

    def validate_run(self, run: Dict, run_index: int) -> bool:
        """Validate a single run in the SARIF file."""
        required_fields = ["tool"]

        for field in required_fields:
            if field not in run:
                self.errors.append(f"Run {run_index}: Missing required field: {field}")
                return False

        # Validate tool
        tool = run.get("tool", {})
        if "driver" not in tool:
            self.errors.append(f"Run {run_index}: Tool missing driver")
            return False

        driver = tool["driver"]
        if "name" not in driver:
            self.errors.append(f"Run {run_index}: Tool driver missing name")
            return False

        # Check for results
        results = run.get("results", [])
        if not isinstance(results, list):
            self.errors.append(f"Run {run_index}: Results must be an array")
            return False

        # Validate each result
        for i, result in enumerate(results):
            if not self.validate_result(result, run_index, i):
                return False

        return True

    def validate_result(self, result: Dict, run_index: int, result_index: int) -> bool:
        """Validate a single result in a run."""
        required_fields = ["ruleId", "message"]

        for field in required_fields:
            if field not in result:
                self.errors.append(
                    f"Run {run_index}, Result {result_index}: Missing required field: {field}"
                )
                return False

        # Validate message
        message = result.get("message", {})
        if not isinstance(message, dict):
            self.errors.append(f"Run {run_index}, Result {result_index}: Message must be an object")
            return False

        if "text" not in message:
            self.errors.append(f"Run {run_index}, Result {result_index}: Message missing text")
            return False

        # Check locations (optional but recommended)
        locations = result.get("locations", [])
        if not locations:
            self.warnings.append(f"Run {run_index}, Result {result_index}: No locations specified")

        return True

    def validate_file(self, file_path: Path) -> Tuple[bool, List[str], List[str]]:
        """Validate a SARIF file."""
        self.errors = []
        self.warnings = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                sarif_data = json.load(f)
        except json.JSONDecodeError as e:
            self.errors.append(f"Invalid JSON: {e}")
            return False, self.errors, self.warnings
        except Exception as e:
            self.errors.append(f"Error reading file: {e}")
            return False, self.errors, self.warnings

        # Validate structure
        if not self.validate_sarif_structure(sarif_data):
            return False, self.errors, self.warnings

        # Validate each run
        for i, run in enumerate(sarif_data["runs"]):
            if not self.validate_run(run, i):
                return False, self.errors, self.warnings

        return len(self.errors) == 0, self.errors, self.warnings


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Validate SARIF files")
    parser.add_argument("files", nargs="+", type=Path, help="SARIF files to validate")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")

    args = parser.parse_args()

    validator = SarifValidator()
    all_valid = True

    logger.info("Validating SARIF files...")
    logger.info("=" * 60)

    for file_path in args.files:
        if not file_path.exists():
            logger.error(f"FAIL {file_path}: File not found")
            all_valid = False
            continue

        is_valid, errors, warnings = validator.validate_file(file_path)

        if is_valid and not warnings:
            logger.info(f"PASS {file_path}: Valid SARIF file")
        elif is_valid and warnings:
            logger.warning(f"WARN {file_path}: Valid with warnings")
            for warning in warnings:
                logger.warning(f"   WARNING: {warning}")
            if args.strict:
                all_valid = False
        else:
            logger.error(f"FAIL {file_path}: Invalid SARIF file")
            for error in errors:
                logger.error(f"   ERROR: {error}")
            for warning in warnings:
                logger.warning(f"   WARNING: {warning}")
            all_valid = False

    logger.info("=" * 60)

    if all_valid:
        logger.info("All SARIF files are valid")
        sys.exit(0)
    else:
        logger.error(
            "Some SARIF files have issues. Please fix them before uploading to GitHub Security tab."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
