#!/usr/bin/env python3
"""Generate pyproject.toml from template using centralized configuration."""

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

# Get project root
project_root = Path(__file__).parent.parent.parent


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
    # Get all values from .project.yml using yq
    package_name = _get_config_value(".project.name")
    package_name_short = _get_config_value(".project.short_name")
    version = _get_config_value(".project.version")
    description = _get_config_value(".project.description")
    author = _get_config_value(".project.author")
    email = _get_config_value(".project.email")
    license_name = _get_config_value(".project.license")
    repo_org = _get_config_value(".repository.org")
    repo_name = _get_config_value(".repository.name")
    python_versions = _get_config_value(".python.versions[]")
    min_python_version = _get_config_value(".python.min_version")

    # Derived values
    repo_url = f"https://github.com/{repo_org}/{repo_name}"
    docs_url = f"https://{repo_org}.github.io/{repo_name}/"
    repo_issues_url = f"{repo_url}/issues"

except Exception as e:
    logging.error(f"Error reading project configuration: {e}")
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
    with open(template_path, encoding="utf-8") as f:
        template_content = f.read()

    # Generate Python classifiers
    python_classifiers = []
    for py_version in python_versions.split("\n"):
        if py_version.strip():
            python_classifiers.append(
                f'    "Programming Language :: Python :: {py_version.strip()}",'
            )
    python_classifiers_str = "\n".join(python_classifiers)

    # Replace placeholders with actual values
    # Use CI VERSION env var if available (for dynamic versioning), otherwise use package version
    version_to_use = os.environ.get("VERSION", version)

    replacements = {
        "{{PACKAGE_NAME}}": package_name,
        "{{PACKAGE_NAME_SHORT}}": package_name_short,
        "{{VERSION}}": version_to_use,
        "{{DESCRIPTION}}": description,
        "{{AUTHOR}}": author,
        "{{EMAIL}}": email,
        "{{REPO_URL}}": repo_url,
        "{{DOCS_URL}}": docs_url,
        "{{REPO_ISSUES_URL}}": repo_issues_url,
        "{{MIN_PYTHON_VERSION}}": min_python_version,
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
    logging.info(f"Package: {package_name} v{version_to_use}")
    logging.info(f"Repository: {repo_url}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate pyproject.toml from template")
    parser.add_argument("--config", help="Configuration file path (unused, for compatibility)")
    args = parser.parse_args()

    generate_pyproject()
