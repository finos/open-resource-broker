"""Platform-specific directory detection for ORB configuration."""

import os
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

    # Symlink venv detection (uv, mise, etc.)
    # Don't resolve symlinks - we want to check the actual executable location
    executable_path = Path(sys.executable)  # Don't resolve!
    prefix_path = Path(sys.prefix)

    try:
        # Check if executable is under prefix
        executable_path.relative_to(prefix_path)
        return False  # Executable is under prefix, not a venv
    except ValueError:
        # Executable is outside prefix, it's a symlink venv
        return True


def is_user_install() -> bool:
    """Check if this is a user install (pip install --user)."""
    return sys.prefix.startswith(str(Path.home()))


def is_system_install() -> bool:
    """Check if this is a system install."""
    return sys.prefix.startswith(("/usr", "/opt"))


def get_config_location() -> Path:
    """Get basic configuration directory location for bootstrap."""
    # 1. Environment override
    if env_dir := os.environ.get("ORB_CONFIG_DIR"):
        return Path(env_dir)

    # 2. Virtual environment (check BEFORE user install)
    if in_virtualenv():
        # For symlink venvs (uv/mise), use executable's grandparent's sibling
        # For standard venvs, use sys.prefix parent
        if sys.prefix != sys.base_prefix:
            # Standard venv: sys.prefix is the venv directory
            return Path(sys.prefix).parent / "config"
        else:
            # Symlink venv: executable is .venv/bin/python, we want .venv/../config
            # .parent = .venv/bin, .parent = .venv, .parent = parent dir
            return Path(sys.executable).parent.parent.parent / "config"

    # 3. Development mode
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / "pyproject.toml").exists():
            return parent / "config"

    # 4. User installation
    if is_user_install():
        return Path.home() / ".local" / "orb" / "config"

    # 5. System installation
    if is_system_install():
        return Path(sys.prefix) / "orb" / "config"

    # 6. Fallback
    return cwd / "config"


def get_work_location() -> Path:
    """Get basic work directory location for bootstrap."""
    # 1. Environment override
    if env_dir := os.environ.get("ORB_WORK_DIR"):
        return Path(env_dir)

    # 2. Relative to config
    return get_config_location().parent / "work"


def get_logs_location() -> Path:
    """Get basic logs directory location for bootstrap."""
    # 1. Environment override
    if env_dir := os.environ.get("ORB_LOG_DIR"):
        return Path(env_dir)

    # 2. Relative to config
    return get_config_location().parent / "logs"


def get_scripts_location() -> Path:
    """Get basic scripts directory location for bootstrap."""
    return get_config_location().parent / "scripts"
