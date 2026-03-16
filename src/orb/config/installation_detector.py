"""Installation mode detection using modern Python APIs."""

import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Literal, Optional, Tuple


def detect_installation_mode(package_name: str = "orb-py") -> Tuple[str, Optional[Path]]:
    """Detect installation mode using importlib.metadata and PEP 610.

    Returns:
        Tuple of (mode, base_path) where mode is one of:
        - 'development': Running from source
        - 'editable': Editable install (pip install -e)
        - 'user': User install (pip install --user)
        - 'system': System/venv install
    """
    try:
        import site

        dist = importlib.metadata.distribution(package_name)
        dist_path = Path(dist._path) if hasattr(dist, "_path") else None  # type: ignore[attr-defined]

        if not dist_path:
            return "development", None

        # Check for PEP 610 editable install (newer pip versions)
        direct_url_file = dist_path / "direct_url.json"
        if direct_url_file.exists():
            try:
                with open(direct_url_file) as f:
                    direct_url = json.load(f)

                dir_info = direct_url.get("dir_info", {})
                if dir_info.get("editable", False):
                    # Extract source path from file:// URL
                    source_url = direct_url.get("url", "")
                    if source_url.startswith("file://"):
                        source_path = source_url[7:]  # Remove file://
                        return "editable", Path(source_path)
            except (json.JSONDecodeError, OSError):
                pass

        # Check for older editable install (egg-info in source tree)
        # If dist_path is within current working directory or has .egg-info suffix
        cwd = Path.cwd()
        if str(dist_path).startswith(str(cwd)) or dist_path.name.endswith(".egg-info"):
            # This is likely an editable install
            if dist_path.name.endswith(".egg-info"):
                # Source directory is parent of egg-info directory
                # For src/package.egg-info -> project_root
                source_path = dist_path.parent.parent
                return "editable", source_path
            else:
                return "editable", cwd

        # Check for user install
        user_site = getattr(site, "USER_SITE", None)
        if user_site is not None and user_site in str(dist_path):
            user_base = getattr(site, "USER_BASE", None)
            return "user", Path(user_base) if user_base is not None else None

        # System or venv install
        return "system", Path(sys.prefix)

    except Exception:
        # Package not installed - running from source
        return "development", None


def is_mise_install() -> bool:
    """True if running under a mise-managed Python."""
    return "/.local/share/mise/" in str(Path(sys.executable).resolve())


def detect_install_mode() -> Literal[
    "development", "editable", "user", "uv_tool", "mise", "system", "venv"
]:
    """Detect installation mode, returning a canonical literal.

    Checks uv tool install first (before venv detection would misclassify it),
    then delegates to detect_installation_mode() for the remaining cases.

    Returns one of: 'development', 'editable', 'user', 'uv_tool', 'mise', 'system', 'venv'
    """
    import site

    # uv tool installs look like a venv (prefix != base_prefix) but live under
    # ~/.local/share/uv/tools/ — intercept before the venv branch fires.
    if "/.local/share/uv/tools/" in str(sys.prefix):
        return "uv_tool"

    # mise-managed Python: executable resolves through ~/.local/share/mise/
    if is_mise_install():
        return "mise"

    # Standard venv: prefix differs from base_prefix.
    if sys.prefix != sys.base_prefix:
        return "venv"

    # User install: sys.prefix starts with site.USER_BASE (mirrors is_user_install()).
    # Check this before importlib.metadata so patched sys.prefix/USER_BASE is respected.
    user_base = getattr(site, "USER_BASE", None)
    if user_base and str(sys.prefix).startswith(user_base):
        return "user"

    # System install: prefix under /usr or /opt (mirrors is_system_install()).
    # Check before importlib.metadata for the same reason.
    if str(sys.prefix).startswith(("/usr", "/opt")):
        return "system"

    mode, _ = detect_installation_mode()

    if mode == "development":
        return "development"
    if mode == "editable":
        return "editable"
    if mode == "user":
        return "user"

    return "system"


def get_template_location() -> Path:
    """Get default_config.json path using importlib.resources."""
    from importlib.resources import as_file, files

    with as_file(files("orb.config").joinpath("default_config.json")) as p:
        return p


def get_scripts_location() -> Path:
    """Get scripts directory location based on installation mode."""
    import sysconfig

    mode, base_path = detect_installation_mode()

    if mode == "development":
        try:
            from orb.application.ports.scheduler_port import SchedulerPort
            from orb.infrastructure.di.container import get_container, is_container_ready

            if is_container_ready():
                strategy = get_container().get(SchedulerPort)
                scripts_dir = strategy.get_scripts_directory()
                if scripts_dir is not None:
                    return scripts_dir
        except Exception:
            pass  # Best-effort: DI container may not be ready during installation detection

        from orb.config.platform_dirs import get_root_location

        return get_root_location() / "scripts"

    elif mode == "editable":
        from orb._package import PACKAGE_ROOT

        return (
            (base_path or Path.cwd())
            / PACKAGE_ROOT
            / "infrastructure/scheduler/hostfactory/scripts"
        )

    elif mode == "user":
        return Path.home() / ".orb" / "scripts"

    else:  # system/venv
        data_path = Path(sysconfig.get_path("data"))
        return data_path / "orb_scripts"
