"""Adapter implementing PathResolutionPort using platform_dirs."""

from orb.domain.base.ports.path_resolution_port import PathResolutionPort


class PathResolutionAdapter(PathResolutionPort):
    """Resolves application directory paths using platform-specific logic."""

    def __init__(self, config_manager=None) -> None:
        self._config_manager = config_manager

    def get_config_dir(self) -> str:
        if self._config_manager is not None:
            return str(self._config_manager.get_config_dir())
        from orb.config.platform_dirs import get_config_location

        return str(get_config_location())

    def get_work_dir(self) -> str:
        if self._config_manager is not None:
            return str(self._config_manager.get_work_dir())
        from orb.config.platform_dirs import get_work_location

        return str(get_work_location())

    def get_logs_dir(self) -> str:
        if self._config_manager is not None:
            return str(self._config_manager.get_log_dir())
        from orb.config.platform_dirs import get_logs_location

        return str(get_logs_location())
