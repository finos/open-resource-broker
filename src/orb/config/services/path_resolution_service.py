"""Centralized path resolution service for configuration files.

This service eliminates duplication of path resolution logic across:
- ConfigurationLoader
- ConfigurationManager
- TemplateConfigurationManager

Architecture:
- Single source of truth for path resolution
- Consistent priority: explicit path > scheduler dir > default dir
- Supports all file types: conf, template, legacy, log, work, events, snapshots
"""

import os
from typing import Optional

from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


class PathResolutionService:
    """Centralized service for resolving configuration file paths."""

    # Default directory mappings
    DEFAULT_DIRS = {
        "config": "config",
        "template": "config",
        "legacy": "config",
        "log": "logs",
        "work": "data",
        "events": "events",
        "snapshots": "snapshots",
    }

    def __init__(self, scheduler_directory_provider=None):
        """Initialize path resolution service.

        Args:
            scheduler_directory_provider: Optional callable that provides scheduler directories
                                         Signature: (file_type: str) -> Optional[str]
        """
        self.scheduler_directory_provider = scheduler_directory_provider

    def resolve_file_path(
        self,
        file_type: str,
        filename: str,
        explicit_path: Optional[str] = None,
    ) -> str:
        """Resolve file path with consistent priority.

        Priority order:
        1. Explicit path (if provided and contains directory)
        2. Scheduler-provided directory + filename (if available)
        3. Default directory + filename (fallback)

        Args:
            file_type: Type of file ('config', 'template', 'legacy', 'log', 'work', 'events', 'snapshots')
            filename: Name of the file
            explicit_path: Explicit path provided by user (optional)

        Returns:
            Resolved file path (may not exist - caller decides how to handle)
        """
        logger.debug(
            "Resolving file path: type=%s, filename=%s, explicit_path=%s",
            file_type,
            filename,
            explicit_path,
        )

        # 1. If explicit path provided and contains directory, use it directly
        if explicit_path and os.path.dirname(explicit_path):
            logger.debug("Using explicit path with directory: %s", explicit_path)
            return explicit_path

        # If explicit_path is just a filename, use it as the filename
        if explicit_path and not os.path.dirname(explicit_path):
            filename = explicit_path
            logger.debug("Using explicit filename: %s", filename)

        # 2. Try scheduler-provided directory + filename
        scheduler_path = self._try_scheduler_directory(file_type, filename)
        if scheduler_path:
            logger.debug("Using scheduler directory path: %s", scheduler_path)
            return scheduler_path

        # 3. Fall back to default directory + filename
        fallback_path = self._get_default_path(file_type, filename)
        logger.debug("Using fallback path: %s", fallback_path)
        return fallback_path

    def _try_scheduler_directory(self, file_type: str, filename: str) -> Optional[str]:
        """Try to get path from scheduler directory.

        Args:
            file_type: Type of file
            filename: Name of the file

        Returns:
            Path if scheduler directory available, None otherwise
        """
        if not self.scheduler_directory_provider:
            return None

        try:
            scheduler_dir = self.scheduler_directory_provider(file_type)
            if scheduler_dir:
                return os.path.join(scheduler_dir, filename)
        except Exception as e:
            logger.debug("Failed to get scheduler directory: %s", e)

        return None

    def _get_default_path(self, file_type: str, filename: str) -> str:
        """Get default path for file type.

        Uses XDG/platform-scoped directories rather than the process working
        directory so that paths are stable regardless of where the process was
        launched from.

        Args:
            file_type: Type of file
            filename: Name of the file

        Returns:
            Default path resolved via platform dirs
        """
        base_dir = self._get_platform_dir(file_type)
        return os.path.join(base_dir, filename)

    @staticmethod
    def _get_platform_dir(file_type: str) -> str:
        """Resolve the platform-scoped base directory for a file type.

        Args:
            file_type: One of 'config', 'template', 'legacy', 'log', 'work',
                       'events', 'snapshots'

        Returns:
            Absolute directory path from platform_dirs
        """
        from orb.config.platform_dirs import (
            get_config_location,
            get_logs_location,
            get_work_location,
        )

        if file_type in ("config", "template", "legacy"):
            return str(get_config_location())
        if file_type == "log":
            return str(get_logs_location())
        if file_type in ("work", "data", "events", "snapshots"):
            return str(get_work_location())
        # Unknown type — fall back to config location
        return str(get_config_location())

    def resolve_directory(self, file_type: str) -> str:
        """Resolve directory for file type.

        Args:
            file_type: Type of file

        Returns:
            Resolved directory path
        """
        # Try scheduler directory first
        if self.scheduler_directory_provider:
            try:
                scheduler_dir = self.scheduler_directory_provider(file_type)
                if scheduler_dir:
                    return scheduler_dir
            except Exception as e:
                logger.debug("Failed to get scheduler directory: %s", e)

        # Fall back to platform-scoped directory
        return self._get_platform_dir(file_type)

    def find_file_with_fallbacks(
        self,
        file_type: str,
        filenames: list[str],
    ) -> Optional[str]:
        """Find first existing file from list of candidates.

        Args:
            file_type: Type of file
            filenames: List of candidate filenames (in priority order)

        Returns:
            Path to first existing file, or None if none found
        """
        for filename in filenames:
            path = self.resolve_file_path(file_type, filename)
            if os.path.exists(path):
                logger.info("Found file: %s", path)
                return path
            logger.debug("File not found: %s", path)

        return None


def create_path_resolution_service(scheduler_directory_provider=None) -> PathResolutionService:
    """Factory function for creating PathResolutionService.

    Args:
        scheduler_directory_provider: Optional callable for scheduler directories

    Returns:
        PathResolutionService instance
    """
    return PathResolutionService(scheduler_directory_provider)
