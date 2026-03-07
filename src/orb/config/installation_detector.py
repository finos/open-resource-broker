"""Installation mode detection using modern Python APIs."""

import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Optional, Tuple


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


def get_template_location() -> Path:
    """Get template file location based on installation mode."""
    import sysconfig

    mode, base_path = detect_installation_mode()

    if mode == "development":
        # Use existing platform_dirs logic
        from orb.config.platform_dirs import get_config_location

        return get_config_location() / "default_config.json"

    elif mode == "editable":
        # Use source directory from PEP 610
        return (base_path or Path.cwd()) / "config" / "default_config.json"

    elif mode == "user":
        # User install - use posix_user scheme
        try:
            scheme = "posix_user" if sys.platform != "win32" else "nt_user"
            data_path = Path(sysconfig.get_path("data", scheme))
        except Exception:
            data_path = base_path if base_path else Path.home() / ".local"
        return data_path / "orb_config" / "default_config.json"

    else:  # system/venv
        # Use default scheme
        data_path = Path(sysconfig.get_path("data"))
        return data_path / "orb_config" / "default_config.json"


def get_scripts_location() -> Path:
    """Get scripts directory location based on installation mode."""
    import sysconfig

    mode, base_path = detect_installation_mode()

    if mode == "development":
        try:
            from orb.domain.base.ports.scheduler_port import SchedulerPort
            from orb.infrastructure.di.container import get_container, is_container_ready

            if is_container_ready():
                strategy = get_container().get(SchedulerPort)
                scripts_dir = strategy.get_scripts_directory()
                if scripts_dir is not None:
                    return scripts_dir
        except Exception:
            pass

        from orb.config.platform_dirs import get_config_location

        return get_config_location().parent / "scripts"

    elif mode == "editable":
        from orb._package import PACKAGE_ROOT

        return (
            (base_path or Path.cwd())
            / PACKAGE_ROOT
            / "infrastructure/scheduler/hostfactory/scripts"
        )

    elif mode == "user":
        try:
            scheme = "posix_user" if sys.platform != "win32" else "nt_user"
            data_path = Path(sysconfig.get_path("data", scheme))
        except Exception:
            data_path = base_path if base_path else Path.home() / ".local"
        return data_path / "orb_scripts"

    else:  # system/venv
        data_path = Path(sysconfig.get_path("data"))
        return data_path / "orb_scripts"
