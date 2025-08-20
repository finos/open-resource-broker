"""Configuration path resolution utilities."""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class ConfigPathResolver:
    """Handles path resolution for configuration directories and files."""

    def __init__(self, base_config_path: Optional[str] = None) -> None:
        """Initialize the instance."""
        self._base_config_path = base_config_path

    def resolve_path(
        self, path_type: str, default_path: str, config_path: Optional[str] = None
    ) -> str:
        """
        Resolve configuration path with fallback logic.

        Args:
            path_type: Type of path (work, conf, log, etc.)
            default_path: Default path to use
            config_path: Optional override path

        Returns:
            Resolved absolute path
        """
        if config_path:
            path = config_path
        else:
            # Use base config path if available
            if self._base_config_path:
                base_dir = os.path.dirname(self._base_config_path)
                path = os.path.join(base_dir, default_path)
            else:
                path = default_path

        # Expand user home directory
        path = os.path.expanduser(path)

        # Convert to absolute path
        abs_path = os.path.abspath(path)

        # Ensure directory exists
        try:
            os.makedirs(abs_path, exist_ok=True)
        except OSError as e:
            logger.warning("Could not create directory %s: %s", abs_path, e)

        return abs_path

    def resolve_file(
        self,
        file_type: str,
        filename: str,
        default_dir: Optional[str] = None,
        config_path: Optional[str] = None,
    ) -> str:
        """
        Resolve configuration file path.

        Args:
            file_type: Type of file (config, log, etc.)
            filename: Name of the file
            default_dir: Default directory for the file
            config_path: Optional override path

        Returns:
            Resolved absolute file path
        """
        if config_path:
            if os.path.isabs(config_path):
                return config_path
            else:
                # Relative to base config directory
                if self._base_config_path:
                    base_dir = os.path.dirname(self._base_config_path)
                    return os.path.abspath(os.path.join(base_dir, config_path))
                else:
                    return os.path.abspath(config_path)

        # Use default directory
        if default_dir:
            directory = self.resolve_path(file_type, default_dir)
            return os.path.join(directory, filename)
        else:
            return os.path.abspath(filename)

    def get_work_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get work directory path."""
        default = default_path or "work"
        return self.resolve_path("work", default, config_path)

    def get_conf_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get configuration directory path."""
        default = default_path or "conf"
        return self.resolve_path("conf", default, config_path)

    def get_log_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get log directory path."""
        default = default_path or "logs"
        return self.resolve_path("log", default, config_path)

    def get_events_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get events directory path."""
        default = default_path or "events"
        return self.resolve_path("events", default, config_path)

    def get_snapshots_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get snapshots directory path."""
        default = default_path or "snapshots"
        return self.resolve_path("snapshots", default, config_path)
