#!/usr/bin/env python3
"""
Security Check Script

Runs comprehensive security checks including:
- Bandit (security linter)
- Safety (dependency vulnerability check)
- Optional: Trivy, Semgrep, TruffleHog

Usage:
    python dev-tools/scripts/security_check.py [--quick] [--container] [--all]

Options:
    --quick      Run only fast checks (bandit, safety)
    --container  Include container security scans
    --all        Run all available security tools
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def run_tool(tool_name, *args):
    """Run a tool using the run-tool script."""
    script_dir = Path(__file__).parent
    run_tool_script = script_dir / "run_tool.sh"

    command = [str(run_tool_script), tool_name] + list(args)

    logger.info(f"Running {tool_name}...")
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            logger.info(f"PASS {tool_name} passed")
            return True
        else:
            logger.warning(f"WARN {tool_name} found issues")
            if result.stdout:
                logger.info(result.stdout)
            if result.stderr:
                logger.warning(result.stderr)
            return False
    except Exception as e:
        logger.error(f"FAIL {tool_name} failed: {e}")
        return False


def run_bandit():
    """Run Bandit security linter."""
    logger.info("=== Bandit Security Linter ===")

    # Run bandit with JSON output
    json_success = run_tool("bandit", "-r", "src", "-f", "json", "-o", "bandit-report.json")

    # Run bandit with SARIF output
    sarif_success = run_tool("bandit", "-r", "src", "-f", "sarif", "-o", "bandit-results.sarif")

    if not json_success or not sarif_success:
        logger.warning("Security issues found - check bandit-report.json")

    return json_success and sarif_success


def run_safety():
    """Run Safety dependency vulnerability check."""
    logger.info("=== Safety Dependency Check ===")

    success = run_tool("safety", "check")
    if not success:
        logger.warning("Vulnerable dependencies found")

    return success


def run_semgrep():
    """Run Semgrep static analysis."""
    logger.info("=== Semgrep Static Analysis ===")

    # Check if semgrep is available
    try:
        subprocess.run(["semgrep", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning("Semgrep not available - install with: pip install semgrep")
        return True  # Don't fail if tool not available

    try:
        result = subprocess.run(
            ["semgrep", "--config=auto", "--sarif", "--output=semgrep.sarif", "src"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info("PASS Semgrep passed")
            return True
        else:
            logger.warning("WARN Semgrep found issues")
            return False
    except Exception as e:
        logger.error(f"FAIL Semgrep failed: {e}")
        return False


def run_trufflehog():
    """Run TruffleHog secrets scan."""
    logger.info("=== TruffleHog Secrets Scan ===")

    # Check if trufflehog is available
    try:
        subprocess.run(["trufflehog", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning(
            "TruffleHog not available - install from https://github.com/trufflesecurity/trufflehog"
        )
        return True  # Don't fail if tool not available

    try:
        result = subprocess.run(
            ["trufflehog", "git", "file://.", "--json"], capture_output=True, text=True
        )

        # Write results to file
        with open("trufflehog-results.json", "w") as f:
            f.write(result.stdout)

        if result.returncode == 0:
            logger.info("PASS TruffleHog passed")
            return True
        else:
            logger.warning("WARN TruffleHog found secrets")
            return False
    except Exception as e:
        logger.error(f"FAIL TruffleHog failed: {e}")
        return False


def main():
    """Main security check function."""
    parser = argparse.ArgumentParser(description="Run security checks")
    parser.add_argument("--quick", action="store_true", help="Run only fast checks")
    parser.add_argument("--container", action="store_true", help="Include container scans")
    parser.add_argument("--all", action="store_true", help="Run all security tools")

    args = parser.parse_args()

    logger.info("=== Security Check ===")

    # Always run core security tools
    results = []
    results.append(run_bandit())
    results.append(run_safety())

    # Run additional tools if requested
    if args.all or not args.quick:
        results.append(run_semgrep())
        results.append(run_trufflehog())

    # Container scans
    if args.container:
        logger.info("=== Container Security Scans ===")
        logger.info("Running container security scans via Makefile...")
        try:
            subprocess.run(["make", "security-container"], check=True)
            logger.info("PASS Container security scans completed")
        except subprocess.CalledProcessError:
            logger.warning("WARN Container security scans had issues")
            results.append(False)
        else:
            results.append(True)

    # Summary
    passed = sum(results)
    total = len(results)

    logger.info(f"\n=== Security Check Summary ===")
    logger.info(f"Passed: {passed}/{total} checks")

    if passed == total:
        logger.info("PASS All security checks passed!")
        return 0
    else:
        logger.warning("WARN Some security checks found issues")
        return 1


if __name__ == "__main__":
    sys.exit(main())
