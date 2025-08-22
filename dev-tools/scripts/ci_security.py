#!/usr/bin/env python3
"""CI security scanning operations."""

import shutil
import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str]) -> int:
    """Run command and return exit code."""
    try:
        subprocess.run(cmd, check=True)
        return 0
    except subprocess.CalledProcessError as e:
        return e.returncode
    except FileNotFoundError:
        print(f"Error: Command not found: {cmd[0]}")
        return 1


def trivy_scan() -> int:
    """Run Trivy container scan."""
    if not shutil.which("docker"):
        print("Docker not available - Trivy requires Docker")
        return 1

    print("Running Trivy container scan...")
    if run_command(["docker", "build", "-t", "security-scan:latest", "."]) != 0:
        return 1

    return run_command(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            "-v",
            f"{Path.cwd()}:/workspace",
            "aquasec/trivy:latest",
            "image",
            "security-scan:latest",
        ]
    )


def hadolint_scan() -> int:
    """Run Hadolint Dockerfile scan."""
    if not shutil.which("hadolint"):
        print("Hadolint not available - install with: brew install hadolint")
        return 1

    print("Running Hadolint Dockerfile scan...")
    return run_command(["hadolint", "Dockerfile"])


def semgrep_scan() -> int:
    """Run Semgrep static analysis."""
    if not shutil.which("semgrep"):
        print("Semgrep not available - install with: pip install semgrep")
        return 1

    print("Running Semgrep static analysis...")
    try:
        subprocess.run(
            ["semgrep", "--config=auto", "--sarif", "--output=semgrep.sarif", "src"], check=True
        )
        return 0
    except subprocess.CalledProcessError:
        print("Semgrep issues found")
        return 0  # Don't fail for semgrep issues


def trivy_fs_scan() -> int:
    """Run Trivy filesystem scan."""
    if not shutil.which("trivy"):
        print("Trivy not available - install from https://aquasecurity.github.io/trivy/")
        return 1

    print("Running Trivy filesystem scan...")
    try:
        subprocess.run(
            [
                "trivy",
                "fs",
                "--skip-dirs",
                ".venv",
                "--format",
                "sarif",
                "--output",
                "trivy-fs-results.sarif",
                ".",
            ],
            check=True,
        )
        return 0
    except subprocess.CalledProcessError:
        print("Trivy filesystem issues found")
        return 0  # Don't fail for trivy issues


def trufflehog_scan() -> int:
    """Run TruffleHog secrets scan."""
    if not shutil.which("trufflehog"):
        print(
            "TruffleHog not available - install from https://github.com/trufflesecurity/trufflehog"
        )
        return 1

    print("Running TruffleHog secrets scan...")
    try:
        with open("trufflehog-results.json", "w") as f:
            subprocess.run(["trufflehog", "git", "file://.", "--json"], stdout=f, check=True)
        return 0
    except subprocess.CalledProcessError:
        print("Secrets found")
        return 0  # Don't fail for secrets found


def main() -> int:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="CI security scanning operations")
    parser.add_argument(
        "scan_type",
        choices=["trivy", "hadolint", "semgrep", "trivy-fs", "trufflehog"],
        help="Type of security scan to run",
    )

    args = parser.parse_args()

    if args.scan_type == "trivy":
        return trivy_scan()
    elif args.scan_type == "hadolint":
        return hadolint_scan()
    elif args.scan_type == "semgrep":
        return semgrep_scan()
    elif args.scan_type == "trivy-fs":
        return trivy_fs_scan()
    elif args.scan_type == "trufflehog":
        return trufflehog_scan()

    return 1


if __name__ == "__main__":
    sys.exit(main())
