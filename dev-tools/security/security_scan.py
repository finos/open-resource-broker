#!/usr/bin/env python3
"""
Comprehensive security scanning script for Open Host Factory Plugin.

This script orchestrates multiple security tools to provide comprehensive
security analysis including SAST, dependency scanning, container security,
and SBOM generation.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class SecurityScanner:
    """Comprehensive security scanner for the project."""

    def __init__(self, project_root: Path):
        """Initialize security scanner."""
        self.project_root = project_root
        self.results = {}
        self.sarif_files = []

    def run_bandit(self) -> Tuple[bool, str]:
        """Run Bandit security linter with SARIF output."""
        print("Running Bandit security analysis...")

        try:
            # Check if bandit-sarif-formatter is available
            try:
                import bandit_sarif_formatter

                sarif_available = True
            except ImportError:
                sarif_available = False
                print("WARNING: bandit-sarif-formatter not available, falling back to JSON")

            # Generate JSON output (always)
            subprocess.run(
                ["python", "-m", "bandit", "-r", "src/", "-f", "json", "-o", "bandit-report.json"],
                cwd=self.project_root,
                check=False,
            )

            # Generate SARIF output if formatter is available
            if sarif_available:
                subprocess.run(
                    [
                        "python",
                        "-m",
                        "bandit",
                        "-r",
                        "src/",
                        "-f",
                        "sarif",
                        "-o",
                        "bandit-report.sarif",
                    ],
                    cwd=self.project_root,
                    check=False,
                )

                self.sarif_files.append("bandit-report.sarif")
                print("SUCCESS: Bandit SARIF report generated for GitHub Security integration")

            return True, "Bandit scan completed"

        except Exception as e:
            return False, f"Bandit scan failed: {e}"

    def run_safety(self) -> Tuple[bool, str]:
        """Run Safety dependency vulnerability check."""
        print("Running Safety dependency scan...")

        try:
            result = subprocess.run(
                ["python", "-m", "safety", "check", "--json"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
            )

            with open(self.project_root / "safety-report.json", "w") as f:
                f.write(result.stdout)

            return True, "Safety scan completed"

        except Exception as e:
            return False, f"Safety scan failed: {e}"

    def run_trivy(self) -> Tuple[bool, str]:
        """Run Trivy container vulnerability scan."""
        print("Running Trivy container security scan...")

        try:
            # Build image
            subprocess.run(
                ["docker", "build", "-t", "security-scan:latest", "."],
                cwd=self.project_root,
                check=True,
            )

            # SARIF output
            subprocess.run(
                [
                    "trivy",
                    "image",
                    "--format",
                    "sarif",
                    "--output",
                    "trivy-results.sarif",
                    "security-scan:latest",
                ],
                cwd=self.project_root,
                check=False,
            )

            # JSON output
            subprocess.run(
                [
                    "trivy",
                    "image",
                    "--format",
                    "json",
                    "--output",
                    "trivy-results.json",
                    "security-scan:latest",
                ],
                cwd=self.project_root,
                check=False,
            )

            self.sarif_files.append("trivy-results.sarif")
            return True, "Trivy scan completed"

        except Exception as e:
            return False, f"Trivy scan failed: {e}"

    def run_hadolint(self) -> Tuple[bool, str]:
        """Run Hadolint Dockerfile security scan."""
        print("Running Hadolint Dockerfile scan...")

        try:
            subprocess.run(
                ["hadolint", "Dockerfile", "--format", "sarif"],
                cwd=self.project_root,
                stdout=open(self.project_root / "hadolint-results.sarif", "w"),
                check=False,
            )

            self.sarif_files.append("hadolint-results.sarif")
            return True, "Hadolint scan completed"

        except Exception as e:
            return False, f"Hadolint scan failed: {e}"

    def generate_sbom(self) -> Tuple[bool, str]:
        """Generate Software Bill of Materials."""
        print("Generating SBOM files...")

        try:
            # Python dependencies SBOM with pip-audit (only CycloneDX format is supported)
            subprocess.run(
                [
                    "python",
                    "-m",
                    "pip_audit",
                    "--format=cyclonedx-json",
                    "--output=python-sbom-cyclonedx.json",
                ],
                cwd=self.project_root,
                check=False,
            )

            # Check if Syft is available
            if subprocess.run(["which", "syft"], capture_output=True).returncode == 0:
                # Project SBOM with Syft
                subprocess.run(
                    ["syft", ".", "-o", "spdx-json=project-sbom-spdx.json"],
                    cwd=self.project_root,
                    check=False,
                )

                subprocess.run(
                    ["syft", ".", "-o", "cyclonedx-json=project-sbom-cyclonedx.json"],
                    cwd=self.project_root,
                    check=False,
                )
            else:
                print("Syft not available - skipping project SBOM generation")

            return True, "SBOM generation completed"

        except Exception as e:
            return False, f"SBOM generation failed: {e}"

    def generate_report(self) -> str:
        """Generate comprehensive security report."""
        print("Generating security report...")

        report = {
            "scan_timestamp": subprocess.check_output(["date", "-u"]).decode().strip(),
            "project": "Open Host Factory Plugin",
            "scans_performed": [],
            "sarif_files": self.sarif_files,
            "results": self.results,
            "recommendations": [
                "Review all SARIF files for detailed security findings",
                "Address high and critical severity vulnerabilities first",
                "Update dependencies with known vulnerabilities",
                "Review container base image for security updates",
                "Implement security controls for identified issues",
            ],
        }

        # Write JSON report
        with open(self.project_root / "security-report.json", "w") as f:
            json.dump(report, f, indent=2)

        # Write markdown summary
        md_report = f"""# Security Scan Report

**Generated:** {report['scan_timestamp']}
**Project:** {report['project']}

## Scans Performed

"""

        for scan, (success, message) in self.results.items():
            status = "PASS" if success else "FAIL"
            md_report += f"- **{status}**: {scan} - {message}\n"

        md_report += f"""
## Generated Files

### SARIF Files (for GitHub Security tab)
"""
        for sarif_file in self.sarif_files:
            md_report += f"- `{sarif_file}`\n"

        md_report += """
### Report Files
- `security-report.json` - Detailed JSON report
- `bandit-report.json` - Bandit security issues
- `safety-report.json` - Dependency vulnerabilities
- `trivy-results.json` - Container vulnerabilities
- `*-sbom-*.json` - Software Bill of Materials

## Next Steps

1. Review SARIF files in GitHub Security tab
2. Address high/critical severity issues
3. Update vulnerable dependencies
4. Review container security recommendations
5. Implement security controls for identified risks

## Tools Used

- **Bandit**: Python security linter
- **Safety**: Python dependency vulnerability scanner
- **Trivy**: Container vulnerability scanner
- **Hadolint**: Dockerfile security linter
- **pip-audit**: Python package vulnerability scanner
- **Syft**: SBOM generator
"""

        with open(self.project_root / "security-report.md", "w") as f:
            f.write(md_report)

        return "security-report.md"

    def run_all_scans(self, include_container: bool = True) -> Dict[str, Tuple[bool, str]]:
        """Run all security scans."""
        print("Starting comprehensive security scan...")

        # Core security scans
        self.results["Bandit"] = self.run_bandit()
        self.results["Safety"] = self.run_safety()
        self.results["SBOM Generation"] = self.generate_sbom()

        # Container scans (optional)
        if include_container:
            self.results["Trivy"] = self.run_trivy()
            self.results["Hadolint"] = self.run_hadolint()

        # Generate report
        report_file = self.generate_report()
        print(f"Security report generated: {report_file}")

        return self.results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Comprehensive security scanner")
    parser.add_argument("--no-container", action="store_true", help="Skip container security scans")
    parser.add_argument(
        "--project-root", type=Path, default=Path.cwd(), help="Project root directory"
    )

    args = parser.parse_args()

    scanner = SecurityScanner(args.project_root)
    results = scanner.run_all_scans(include_container=not args.no_container)

    # Print summary
    print("\n" + "=" * 60)
    print("SECURITY SCAN SUMMARY")
    print("=" * 60)

    all_passed = True
    for scan, (success, message) in results.items():
        status = "PASS" if success else "FAIL"
        print(f"{status}: {scan} - {message}")
        if not success:
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("All security scans completed successfully")
        sys.exit(0)
    else:
        print("Some security scans encountered issues. Check the reports.")
        sys.exit(1)


if __name__ == "__main__":
    main()
