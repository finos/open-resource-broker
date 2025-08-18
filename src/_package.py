"""Package metadata - works in both development and production."""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _get_from_project_yml() -> Optional[dict]:
    """Try to read from .project.yml (development mode)."""
    try:
        import yaml

        project_root = Path(__file__).parent.parent
        project_file = project_root / ".project.yml"
        if project_file.exists():
            with open(project_file) as f:
                return yaml.safe_load(f)
    except Exception as e:
        logger.debug("Failed to read project.yml: %s", e)
    return None


def _get_from_package_metadata() -> Optional[dict]:
    """Try to read from package metadata (installed mode)."""
    try:
        from importlib.metadata import metadata, version

        meta = metadata("open-hostfactory-plugin")
        return {
            "project": {
                "name": meta["Name"],
                "version": version("open-hostfactory-plugin"),
                "description": meta["Summary"],
            },
            "repository": {"org": "awslabs", "name": "open-hostfactory-plugin"},
        }
    except Exception as e:
        logger.debug("Failed to read package metadata: %s", e)
    return None


# Try development first, then production
config = _get_from_project_yml() or _get_from_package_metadata()

if not config:
    # Final fallback
    config = {
        "project": {
            "name": "open-hostfactory-plugin",
            "short_name": "ohfp",
            "version": "0.1.0-dev",
            "description": "Cloud provider integration plugin for IBM Spectrum Symphony Host Factory",
        },
        "repository": {"org": "awslabs", "name": "open-hostfactory-plugin", "registry": "ghcr.io"},
    }

# Export the same interface
PACKAGE_NAME = config["project"]["name"]
PACKAGE_NAME_SHORT = config["project"].get("short_name", "ohfp")
__version__ = config["project"]["version"]
VERSION = __version__
DESCRIPTION = config["project"]["description"]
AUTHOR = config["project"].get("author", "AWS Professional Services")
EMAIL = config["project"].get("email", "aws-proserve@amazon.com")

# Repository metadata
REPO_ORG = config["repository"].get("org", "awslabs")
REPO_NAME = config["repository"].get("name", "open-hostfactory-plugin")
CONTAINER_REGISTRY = config["repository"].get("registry", "ghcr.io")

# Derived values
PACKAGE_NAME_PYTHON = PACKAGE_NAME.replace("-", "_")
REPO_URL = f"https://github.com/{REPO_ORG}/{REPO_NAME}"
REPO_ISSUES_URL = f"{REPO_URL}/issues"
DOCS_URL = f"https://{REPO_ORG}.github.io/{REPO_NAME}"
CONTAINER_IMAGE = PACKAGE_NAME
