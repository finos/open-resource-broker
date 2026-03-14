"""Platform-specific directory detection for ORB configuration."""

import os
import site
import sys
from pathlib import Path


def in_virtualenv() -> bool:
    """Check if running in a virtual environment.

    Handles both standard venvs (sys.prefix != sys.base_prefix) and
    symlink-based venvs like uv/mise (sys.executable not in sys.prefix).
    """
    # Standard venv detection
    if sys.prefix != sys.base_prefix:
        return True

    # Symlink venv detection (project-local .venv only)
    # Don't resolve symlinks - we want to check the actual executable location
    executable_path = Path(sys.executable)  # Don't resolve!
    prefix_path = Path(sys.prefix)

    try:
        # Check if executable is under prefix
        executable_path.relative_to(prefix_path)
        return False  # Executable is under prefix, not a venv
    except ValueError:
        # Executable is outside prefix — only treat as symlink-venv if the
        # executable path actually contains a .venv directory component.
        # This excludes mise (~/.local/share/mise/...) and similar tools.
        if ".venv" in executable_path.parts:
            return True
        return False


def is_user_install() -> bool:
    """Check if this is a user install (pip install --user)."""
    user_base = getattr(site, "USER_BASE", None)
    if user_base and str(sys.prefix).startswith(user_base):
        return True
    return False


def is_system_install() -> bool:
    """Check if this is a system install."""
    return sys.prefix.startswith(("/usr", "/opt"))


def get_root_location() -> Path:
    """Single source of truth for platform root directory detection.

    Priority order:
    1. ORB_ROOT_DIR env var
    2. ORB_CONFIG_DIR env var → parent (infer root from config override)
    3. uv tool install (~/.local/share/uv/tools/) → ~/.orb
    4a. virtualenv + mise → ~/.orb
    4b. virtualenv + standard venv (prefix != base_prefix) → sys.prefix parent
    4c. virtualenv + symlink .venv → executable's grandparent's parent
    5. pyproject.toml found in cwd or ancestor → that directory
    6. user install → ~/.orb
    7. system install → sys.prefix/orb
    8. fallback → cwd
    """
    # 1. Explicit root dir override
    if root_dir := os.environ.get("ORB_ROOT_DIR"):
        return Path(root_dir)

    # 2. Config dir override → infer root as its parent
    if config_dir := os.environ.get("ORB_CONFIG_DIR"):
        return Path(config_dir).parent

    # 3. uv tool install: ~/.local/share/uv/tools/<name>/
    if "/.local/share/uv/tools/" in str(sys.prefix):
        return Path.home() / ".orb"

    # 4. Virtual environment (check BEFORE user install)
    if in_virtualenv():
        # 4a. mise-managed Python: executable under ~/.local/share/mise/
        if "/.local/share/mise/" in str(Path(sys.executable)):
            return Path.home() / ".orb"
        # 4b. Standard venv: sys.prefix is the venv directory
        if sys.prefix != sys.base_prefix:
            return Path(sys.prefix).parent
        # 4c. Symlink venv: executable is .venv/bin/python, we want .venv/..
        return Path(sys.executable).parent.parent.parent

    # 5. Development mode: find pyproject.toml
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / "pyproject.toml").exists():
            return parent

    # 6. User installation
    if is_user_install():
        return Path.home() / ".orb"

    # 7. System installation
    if is_system_install():
        return Path(sys.prefix) / "orb"

    # 8. Fallback
    return cwd


def get_config_location() -> Path:
    """Get basic configuration directory location for bootstrap."""
    if env_dir := os.environ.get("ORB_CONFIG_DIR"):
        return Path(env_dir)
    return get_root_location() / "config"


def get_work_location() -> Path:
    """Get basic work directory location for bootstrap."""
    if env_dir := os.environ.get("ORB_WORK_DIR"):
        return Path(env_dir)
    return get_root_location() / "work"


def get_logs_location() -> Path:
    """Get basic logs directory location for bootstrap."""
    if env_dir := os.environ.get("ORB_LOG_DIR"):
        return Path(env_dir)
    return get_root_location() / "logs"


def get_scripts_location() -> Path:
    """Get basic scripts directory location for bootstrap."""
    if env_dir := os.environ.get("ORB_SCRIPTS_DIR"):
        return Path(env_dir)
    return get_root_location() / "scripts"


def get_health_location() -> Path:
    """Get health check directory location."""
    if env_dir := os.environ.get("ORB_HEALTH_DIR"):
        return Path(env_dir)
    return get_work_location() / "health"


def get_cache_location() -> Path:
    """Get cache directory location."""
    if env_dir := os.environ.get("ORB_CACHE_DIR"):
        return Path(env_dir)
    return get_work_location() / ".cache"
