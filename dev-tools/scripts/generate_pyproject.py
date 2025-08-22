#!/usr/bin/env python3
"""Generate pyproject.toml from template using centralized configuration."""

import argparse
import logging
import os
import sys
from pathlib import Path

# Add src to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / "src"))

import subprocess


def _get_config_value(key: str) -> str:
    """Get value from .project.yml using yq."""
    try:
        result = subprocess.run(
            ["yq", key, ".project.yml"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logging.error(f"Error reading config key '{key}': {e}")
        logging.error("Make sure yq is installed and .project.yml exists")
        sys.exit(1)


try:
    from _package import (
        DESCRIPTION,
        DOCS_URL,
        PACKAGE_NAME,
        PACKAGE_NAME_SHORT,
        REPO_ISSUES_URL,
        REPO_URL,
        __version__,
    )

    # Get Python version info from config
    python_versions = _get_config_value(".python.versions[]")
    min_python_version = _get_config_value(".python.min_version")
    author = _get_config_value(".project.author")
    email = _get_config_value(".project.email")

except ImportError as e:
    logging.error(f"Error importing package configuration: {e}")
    logging.error("Make sure yq is installed and .project.yml exists")
    sys.exit(1)


def generate_pyproject():
    """Generate pyproject.toml from template."""
    template_path = project_root / "pyproject.toml.template"
    output_path = project_root / "pyproject.toml"

    if not template_path.exists():
        logging.error(f"Error: Template file not found: {template_path}")
        sys.exit(1)

    # Read template
    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    # Generate Python classifiers
    python_classifiers = []
    for version in python_versions.split("\n"):
        if version.strip():
            python_classifiers.append(f'    "Programming Language :: Python :: {version.strip()}",')
    python_classifiers_str = "\n".join(python_classifiers)

    # Replace placeholders with actual values
    # Use CI VERSION env var if available (for dynamic versioning), otherwise use package version
    version_to_use = os.environ.get("VERSION", __version__)

    replacements = {
        "{{PACKAGE_NAME}}": PACKAGE_NAME,
        "{{PACKAGE_NAME_SHORT}}": PACKAGE_NAME_SHORT,
        "{{VERSION}}": version_to_use,
        "{{DESCRIPTION}}": DESCRIPTION,
        "{{AUTHOR}}": author,
        "{{EMAIL}}": email,
        "{{REPO_URL}}": REPO_URL,
        "{{DOCS_URL}}": DOCS_URL,
        "{{REPO_ISSUES_URL}}": REPO_ISSUES_URL,
        "{{PYTHON_CLASSIFIERS}}": python_classifiers_str,
        "{{MIN_PYTHON_VERSION}}": min_python_version,
    }

    generated_content = template_content
    for placeholder, value in replacements.items():
        generated_content = generated_content.replace(placeholder, value)

    # Write generated file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(generated_content)

    logging.info("Generated pyproject.toml from template")
    logging.info(f"Package: {PACKAGE_NAME} v{__version__}")
    logging.info(f"Repository: {REPO_URL}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate pyproject.toml from template")
    parser.add_argument("--config", help="Configuration file path (unused, for compatibility)")
    args = parser.parse_args()

    generate_pyproject()
