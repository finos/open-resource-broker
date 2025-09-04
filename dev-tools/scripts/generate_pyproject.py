#!/usr/bin/env python3
"""Selective pyproject.toml templating - only updates metadata, preserves dependencies."""

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        logging.error("Neither tomllib nor tomli available. Install tomli: pip install tomli")
        sys.exit(1)

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


def write_toml_section(lines: list, section_name: str, data: Any, indent: int = 0) -> None:
    """Write a TOML section to lines list."""
    prefix = "  " * indent

    if isinstance(data, dict):
        if indent == 0:
            lines.append(f"[{section_name}]")
        else:
            lines.append(f"{prefix}[{section_name}]")

        for key, value in data.items():
            if isinstance(value, dict):
                write_toml_section(lines, f"{section_name}.{key}", value, indent)
            elif isinstance(value, list):
                if all(isinstance(item, str) for item in value):
                    lines.append(f"{prefix}{key} = [")
                    for item in value:
                        lines.append(f'{prefix}    "{item}",')
                    lines.append(f"{prefix}]")
                elif all(isinstance(item, dict) for item in value):
                    lines.append(f"{prefix}{key} = [")
                    for item in value:
                        lines.append(
                            f"{prefix}    {{"
                            + ", ".join(f'{k} = "{v}"' for k, v in item.items())
                            + "},"
                        )
                    lines.append(f"{prefix}]")
            elif isinstance(value, str):
                lines.append(f'{prefix}{key} = "{value}"')
            else:
                lines.append(f"{prefix}{key} = {value}")
        lines.append("")


def update_pyproject_selective(pyproject_path: Path) -> None:
    """Update only metadata sections in pyproject.toml, preserve dependencies."""

    # Get metadata updates
    package_name = _get_config_value(".project.name")
    package_name_short = _get_config_value(".project.short_name")
    version = _get_config_value(".project.version")
    description = _get_config_value(".project.description")
    author = _get_config_value(".project.author")
    email = _get_config_value(".project.email")
    min_python = _get_config_value(".python.versions[0]")
    default_python = _get_config_value(".python.default_version")

    # Generate URLs
    org = _get_config_value(".repository.org")
    repo_name = _get_config_value(".repository.name")
    repo_url = f"https://github.com/{org}/{repo_name}"
    docs_url = f"https://{org}.github.io/{repo_name}/"
    issues_url = f"{repo_url}/issues"

    # Read existing file
    if not pyproject_path.exists():
        logging.error(f"pyproject.toml not found at {pyproject_path}")
        sys.exit(1)

    with open(pyproject_path) as f:
        content = f.read()

    lines = content.split("\n")
    new_lines = []
    skip_section = False
    in_metadata_section = False

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Check if we're entering a metadata section we want to replace
        if line.startswith("[project]"):
            in_metadata_section = True
            # Write updated project metadata
            new_lines.extend(
                [
                    "[project]",
                    f'name = "{package_name}"',
                    f'version = "{version}"',
                    f'description = "{description}"',
                    'readme = "README.md"',
                    'license = "Apache-2.0"',
                    "authors = [",
                    f'    {{name = "{author}", email = "{email}"}},',
                    "]",
                    "maintainers = [",
                    f'    {{name = "{author}", email = "{email}"}},',
                    "]",
                    "keywords = [",
                    '    "aws",',
                    '    "ec2",',
                    '    "hostfactory",',
                    '    "symphony",',
                    '    "hpc",',
                    '    "cluster",',
                    '    "cloud",',
                    '    "infrastructure",',
                    "]",
                    "classifiers = [",
                    '    "Development Status :: 4 - Beta",',
                    '    "Intended Audience :: Developers",',
                    '    "Natural Language :: English",',
                    '    "Operating System :: OS Independent",',
                    '    "Programming Language :: Python :: 3",',
                ]
            )

            # Add Python version classifiers
            python_versions = _get_config_value(".python.versions[]").split("\n")
            for py_version in python_versions:
                if py_version.strip():
                    new_lines.append(
                        f'    "Programming Language :: Python :: {py_version.strip()}",'
                    )

            new_lines.extend(
                [
                    '    "Topic :: Software Development :: Libraries :: Python Modules",',
                    '    "Topic :: System :: Clustering",',
                    '    "Topic :: System :: Distributed Computing",',
                    "]",
                    f'requires-python = ">={min_python}"',
                    "",
                ]
            )

            # Skip until we find dependencies or next section
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(("dependencies", "[")):
                i += 1
            continue

        elif line.startswith("[project.urls]"):
            # Replace URLs section
            new_lines.extend(
                [
                    "[project.urls]",
                    f'Homepage = "{repo_url}"',
                    f'Documentation = "{docs_url}"',
                    f'Repository = "{repo_url}"',
                    f'"Bug Reports" = "{issues_url}"',
                    "",
                ]
            )
            # Skip existing URLs section
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("["):
                i += 1
            continue

        elif line.startswith("[project.scripts]"):
            # Replace scripts section
            new_lines.extend(
                [
                    "[project.scripts]",
                    f'{package_name_short} = "run:cli_main"',
                    f'{package_name} = "run:cli_main"',
                    "",
                ]
            )
            # Skip existing scripts section
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("["):
                i += 1
            continue

        else:
            # Keep all other lines (including dependencies)
            new_lines.append(lines[i])
            i += 1

    # Write updated content
    with open(pyproject_path, "w") as f:
        f.write("\n".join(new_lines))

    logging.info(f"Updated metadata in {pyproject_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate pyproject.toml with selective templating"
    )
    parser.add_argument("--config", default=".project.yml", help="Config file path")
    parser.add_argument("--output", default="pyproject.toml", help="Output file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s"
    )

    # Update pyproject.toml selectively
    output_path = project_root / args.output
    update_pyproject_selective(output_path)

    logging.info("Selective pyproject.toml templating completed")


if __name__ == "__main__":
    main()
