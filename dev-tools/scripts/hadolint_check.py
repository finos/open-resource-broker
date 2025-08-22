#!/usr/bin/env python3
"""Hadolint Dockerfile checker script."""

import sys
import subprocess
import shutil
from pathlib import Path
from typing import List


def check_hadolint_available() -> bool:
    """Check if hadolint is available."""
    return shutil.which("hadolint") is not None


def run_hadolint(dockerfile: Path) -> int:
    """Run hadolint on a Dockerfile."""
    if not dockerfile.exists():
        print(f"Warning: {dockerfile} not found, skipping")
        return 0
    
    print(f"Checking {dockerfile} with hadolint...")
    try:
        result = subprocess.run(["hadolint", str(dockerfile)], check=True)
        return result.returncode
    except subprocess.CalledProcessError as e:
        print(f"Hadolint found issues in {dockerfile}")
        return e.returncode


def main() -> int:
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Check Dockerfiles with hadolint")
    parser.add_argument("files", nargs="*", help="Dockerfile paths to check")
    parser.add_argument("--install-help", action="store_true", help="Show installation help")
    
    args = parser.parse_args()
    
    if args.install_help:
        print("Install hadolint:")
        print("  macOS: brew install hadolint")
        print("  Linux: See https://github.com/hadolint/hadolint#install")
        return 0
    
    if not check_hadolint_available():
        print("Error: hadolint not found")
        print("Install with: brew install hadolint")
        print("Or run: python dev-tools/scripts/hadolint_check.py --install-help")
        return 1
    
    # Default files to check
    files = args.files or ["Dockerfile", "dev-tools/docker/Dockerfile.dev-tools"]
    
    exit_code = 0
    for file_path in files:
        dockerfile = Path(file_path)
        result = run_hadolint(dockerfile)
        if result != 0:
            exit_code = result
    
    if exit_code == 0:
        print("All Dockerfiles passed hadolint checks!")
    
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
