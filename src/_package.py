"""Package metadata and naming constants - centralized from .project.yml."""

import logging
import subprocess
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_config_value(key: str) -> str:
    """Get value from .project.yml using yq."""
    try:
        project_root = Path(__file__).parent.parent
        result = subprocess.run(
            ["yq", key, ".project.yml"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"Error reading config key '{key}': {e}")
        logger.error("Make sure yq is installed and .project.yml exists")
        sys.exit(1)


# Load all values from .project.yml (single source of truth)
PACKAGE_NAME = _get_config_value(".project.name")
PACKAGE_NAME_SHORT = _get_config_value(".project.short_name")
__version__ = _get_config_value(".project.version")  # Version for imports
VERSION = __version__  # Alias for compatibility
DESCRIPTION = _get_config_value(".project.description")

# Repository metadata
REPO_ORG = _get_config_value(".repository.org")
REPO_NAME = _get_config_value(".repository.name")
CONTAINER_REGISTRY = _get_config_value(".repository.registry")

# Derived values
PACKAGE_NAME_PYTHON = PACKAGE_NAME.replace("-", "_")
REPO_URL = f"https://github.com/{REPO_ORG}/{REPO_NAME}"
REPO_ISSUES_URL = f"{REPO_URL}/issues"
DOCS_URL = f"https://{REPO_ORG}.github.io/{REPO_NAME}"
CONTAINER_IMAGE = PACKAGE_NAME
