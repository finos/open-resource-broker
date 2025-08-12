#!/usr/bin/env python3
"""
SBOM generation script.

Generates SBOM files in CycloneDX and SPDX formats.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from typing import List

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def run_command(cmd: List[str], capture_output: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    try:
        return subprocess.run(cmd, capture_output=capture_output, text=True, check=False)
    except Exception as e:
        logger.info(f"Error running command {' '.join(cmd)}: {e}", file=sys.stderr)
        sys.exit(1)


def generate_cyclonedx_sbom(output_file: str) -> bool:
    """Generate CycloneDX SBOM using cyclonedx-py."""
    logger.info(f"Generating CycloneDX SBOM: {output_file}")

    # Use correct cyclonedx-py syntax
    result = run_command(["cyclonedx-py", "environment", "-o", output_file])

    if result.returncode == 0:
        logger.info(f"Generated CycloneDX SBOM: {output_file}")
        return True
    else:
        logger.info(f"Failed to generate CycloneDX SBOM: {result.stderr}", file=sys.stderr)
        return False


def generate_spdx_sbom(output_file: str) -> bool:
    """Generate SPDX SBOM, with fallback if cyclonedx-py doesn't support SPDX."""
    logger.info(f"Generating SPDX SBOM: {output_file}")

    # Try cyclonedx-py with SPDX format first
    help_result = run_command(["cyclonedx-py", "--help"])

    if help_result.returncode == 0 and "spdxjson" in help_result.stdout:
        result = run_command(
            ["cyclonedx-py", "environment", "--format", "spdxjson", "-o", output_file]
        )

        if result.returncode == 0:
            logger.info(f"Generated SPDX SBOM with cyclonedx-py: {output_file}")
            return True

    # Fallback: generate SPDX manually
    return generate_spdx_fallback(output_file)


def generate_spdx_fallback(output_file: str) -> bool:
    """Generate SPDX SBOM using importlib.metadata as fallback."""
    try:
        # Use importlib.metadata instead of deprecated pkg_resources
        try:
            from importlib.metadata import distributions
        except ImportError:
            # Python < 3.8 fallback
            from importlib_metadata import distributions

        # Create basic SPDX structure
        spdx_doc = {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": "open-hostfactory-plugin",
            "documentNamespace": "https://github.com/awslabs/open-hostfactory-plugin",
            "creationInfo": {
                "created": datetime.now(timezone.utc).isoformat(),
                "creators": ["Tool: generate_sbom.py"],
            },
            "packages": [],
        }

        # Add packages from current environment
        for dist in distributions():
            package = {
                "SPDXID": f"SPDXRef-Package-{dist.metadata['Name']}",
                "name": dist.metadata["Name"],
                "version": dist.version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "copyrightText": "NOASSERTION",
            }
            spdx_doc["packages"].append(package)

        # Write SPDX file
        with open(output_file, "w") as f:
            json.dump(spdx_doc, f, indent=2)

        logger.info(f"Generated SPDX SBOM with fallback: {output_file}")
        return True

    except Exception as e:
        logger.info(f"Failed to generate SPDX SBOM: {e}", file=sys.stderr)
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate SBOM files")
    parser.add_argument(
        "--format",
        choices=["cyclonedx", "spdx", "both"],
        default="both",
        help="SBOM format to generate",
    )
    parser.add_argument("--output", help="Output file path (for single format only)")
    parser.add_argument("--output-dir", default=".", help="Output directory for SBOM files")

    args = parser.parse_args()

    success = True

    if args.format in ["cyclonedx", "both"]:
        output_file = (
            args.output
            if args.output and args.format == "cyclonedx"
            else f"{args.output_dir}/python-sbom-cyclonedx.json"
        )
        if not generate_cyclonedx_sbom(output_file):
            success = False

    if args.format in ["spdx", "both"]:
        output_file = (
            args.output
            if args.output and args.format == "spdx"
            else f"{args.output_dir}/python-sbom-spdx.json"
        )
        if not generate_spdx_sbom(output_file):
            success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
