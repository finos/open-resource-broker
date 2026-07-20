"""Main configuration manager - orchestrates all configuration concerns."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, Dict, Optional, TypeVar

# Import config classes for runtime use
from orb.config.schemas import AppConfig
from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.logging.logger import get_logger

from .cache_manager import ConfigCacheManager
from .path_resolver import ConfigPathResolver
from .provider_manager import ProviderConfigManager
from .type_converter import ConfigTypeConverter

if TYPE_CHECKING:
    from orb.config.loader import ConfigurationLoader
    from orb.config.schemas.provider_strategy_schema import ProviderConfig

T = TypeVar("T")
logger = get_logger(__name__)


class ConfigurationManager:
    """
    Centralized configuration manager that serves as the single source of truth.

    This class provides a centralized interface for accessing configuration with:
    - Type safety through dataclasses
    - Support for legacy and new configuration formats
    - Environment variable overrides
    - Configuration validation
    - Lazy loading for performance

    It uses ConfigurationLoader to load configuration from multiple sources.
    """

    def __init__(
        self,
        config_file: Optional[str] = None,
        config_dict: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize configuration manager with lazy loading.

        Args:
            config_file: Path to config file. If None, uses platform default.
            config_dict: In-memory config dict. If provided, file loading is skipped.
        """
        self._config_dict = config_dict

        if config_dict is not None:
            # In-memory config — no file needed
            self._config_file = config_file  # May be None, that's fine
        elif config_file is None:
            from orb.config.platform_dirs import get_config_location

            config_file = str(get_config_location() / "config.json")
            self._config_file = config_file
        else:
            self._config_file = config_file
        self._loader: Optional[ConfigurationLoader] = None
        self._app_config: Optional[AppConfig] = None

        # Initialize component managers
        self._cache_manager = ConfigCacheManager()
        self._raw_config: Optional[Dict[str, Any]] = None
        self._type_converter: Optional[ConfigTypeConverter] = None
        self._path_resolver: Optional[ConfigPathResolver] = None
        self._provider_manager: Optional[ProviderConfigManager] = None

        # Scheduler override support
        self._scheduler_override: Optional[str] = None

    @property
    def config_file(self) -> str | None:
        """Return the config file path this manager was initialised with.

        Prefer this over accessing the private ``_config_file`` attribute
        directly.  Returns ``None`` when the manager was constructed from an
        in-memory dict without a backing file.
        """
        return self._config_file

    @property
    def loader(self) -> ConfigurationLoader:
        """Lazy load configuration loader."""
        if self._loader is None:
            from orb.config.loader import ConfigurationLoader

            self._loader = ConfigurationLoader()
        return self._loader

    @property
    def app_config(self) -> AppConfig:
        """Get application configuration with caching."""
        if self._app_config is None:
            self._app_config = self._load_app_config()
        return self._app_config

    def _load_app_config(self) -> AppConfig:
        """Load application configuration from loader or in-memory dict."""
        try:
            raw_config = self._ensure_raw_config()
            return self.loader.create_app_config(raw_config)
        except Exception as e:
            logger.error("Failed to load app config: %s", e, exc_info=True)
            raise

    def _ensure_raw_config(self) -> dict[str, Any]:
        """Ensure raw configuration is loaded."""
        if self._raw_config is None:
            if self._config_dict is not None:
                from orb.config.loader import ConfigurationLoader

                self._raw_config = ConfigurationLoader._build_raw_config_from_dict(
                    self._config_dict, config_manager=self
                )
            else:
                self._raw_config = self.loader.load(self._config_file, config_manager=self)
        return self._raw_config

    def _ensure_type_converter(self) -> ConfigTypeConverter:
        """Ensure type converter is initialized."""
        if self._type_converter is None:
            raw_config = self._ensure_raw_config()
            self._type_converter = ConfigTypeConverter(raw_config)
        return self._type_converter

    def _ensure_path_resolver(self) -> ConfigPathResolver:
        """Ensure path resolver is initialized."""
        if self._path_resolver is None:
            self._path_resolver = ConfigPathResolver(self._config_file)
        return self._path_resolver

    def _ensure_provider_manager(self) -> ProviderConfigManager:
        """Ensure provider manager is initialized."""
        if self._provider_manager is None:
            raw_config = self._ensure_raw_config()
            self._provider_manager = ProviderConfigManager(raw_config)
        return self._provider_manager

    def get_typed(self, config_type: type[T]) -> T:
        """Get typed configuration with caching."""
        # Check cache first
        cached_config = self._cache_manager.get_cached_config(config_type)
        if cached_config is not None:
            return cached_config

        # Create new typed config
        type_converter = self._ensure_type_converter()
        config_instance = type_converter.get_typed(config_type)

        # Cache the result
        self._cache_manager.cache_config(config_type, config_instance)

        return config_instance

    def get_typed_with_defaults(self, config_type: type[T]) -> T:
        """Get typed configuration with guaranteed defaults."""
        try:
            return self.get_typed(config_type)
        except (ConfigurationError, Exception) as e:
            logger.warning(
                f"Configuration loading failed for {config_type.__name__}: {e}", exc_info=True
            )
            logger.info(f"Using default configuration for {config_type.__name__}")
            return config_type()  # Use Pydantic defaults

    def reload(self) -> None:
        """Reload configuration from sources.

        Forces the next access to ``_ensure_raw_config`` to go to disk via
        the loader instead of rebuilding from the cached ``_config_dict``
        snapshot taken at construction. Without invalidating
        ``_config_dict``, on-disk edits (e.g. after a CLI ``orb init``)
        never propagate to the running server.
        """
        try:
            # Clear all caches
            self._cache_manager.clear_cache()
            self._raw_config = None
            self._app_config = None
            self._type_converter = None
            self._path_resolver = None
            self._provider_manager = None

            # Drop the stale in-memory snapshot so the next access
            # re-reads from disk via the loader. Preserve ``_config_file``
            # so the loader knows what path to read.
            if self._config_file:
                self._config_dict = None

            # Force loader-level reload if the loader supports it.
            if self._loader and hasattr(self._loader, "reload"):
                try:
                    self._loader.reload()  # type: ignore[attr-defined]
                except Exception as loader_exc:
                    logger.warning("Loader reload hook failed: %s", loader_exc)

            # Mark reload time
            self._cache_manager.mark_reload(time.time())

            logger.info("Configuration reloaded successfully")
        except Exception as e:
            logger.error("Failed to reload configuration: %s", e, exc_info=True)
            raise ConfigurationError(f"Configuration reload failed: {e}")

    # Delegate type conversion methods
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key."""
        return self._ensure_type_converter().get(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get boolean configuration value."""
        return self._ensure_type_converter().get_bool(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        """Get integer configuration value."""
        return self._ensure_type_converter().get_int(key, default)

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get float configuration value."""
        return self._ensure_type_converter().get_float(key, default)

    def get_str(self, key: str, default: str = "") -> str:
        """Get string configuration value."""
        return self._ensure_type_converter().get_str(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set configuration value."""
        self._ensure_type_converter().set(key, value)
        # Clear relevant caches
        self._cache_manager.clear_cache()

    def update(self, updates: dict[str, Any]) -> None:
        """Update configuration with new values."""
        self._ensure_type_converter().update(updates)
        # Clear relevant caches
        self._cache_manager.clear_cache()

    # Delegate path resolution methods
    def resolve_path(
        self, path_type: str, default_path: str, config_path: Optional[str] = None
    ) -> str:
        """Resolve configuration path."""
        return self._ensure_path_resolver().resolve_path(path_type, default_path, config_path)

    def get_work_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get work directory path."""
        return self._ensure_path_resolver().get_work_dir(default_path, config_path)

    def get_cache_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get cache directory path."""
        return self._ensure_path_resolver().get_cache_dir(default_path, config_path)

    def get_config_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get configuration directory path."""
        return self._ensure_path_resolver().get_config_dir(default_path, config_path)

    def get_log_dir(
        self, default_path: Optional[str] = None, config_path: Optional[str] = None
    ) -> str:
        """Get log directory path."""
        return self._ensure_path_resolver().get_log_dir(default_path, config_path)

    # Delegate provider management methods
    def get_storage_strategy(self) -> str:
        """Get storage strategy."""
        return self._ensure_provider_manager().get_storage_strategy()

    def get_scheduler_strategy(self) -> str:
        """Get scheduler strategy with override support."""
        if self._scheduler_override:
            return self._scheduler_override
        return self._ensure_provider_manager().get_scheduler_strategy()

    def override_scheduler_strategy(self, scheduler_type: str) -> None:
        """Temporarily override scheduler strategy."""
        self._scheduler_override = scheduler_type

    def restore_scheduler_strategy(self) -> None:
        """Restore original scheduler strategy."""
        self._scheduler_override = None

    def get_loaded_config_file(self) -> str | None:
        """Get the actual config file that was loaded.

        Routes through the single ``platform_dirs.resolve_config_file`` source of
        truth so discovery order (explicit path, ORB_CONFIG_FILE, ORB_CONFIG_DIR,
        platform-dirs location, ~/.orb fallback) stays consistent with the
        startup validator and every other consumer.

        This is a *best-effort discovery* used for reporting the effective
        config file. It may point at a candidate that exists on disk but was
        not the source this manager loaded (e.g. an ``ORB_CONFIG_FILE`` set
        after construction). Callers that need the exact source this manager
        read — for example to choose a safe default save target — must use
        :meth:`get_source_config_file` instead.
        """
        from orb.config.platform_dirs import resolve_config_file

        resolved = resolve_config_file("config.json", explicit_path=self._config_file)
        return str(resolved) if resolved is not None else None

    def get_source_config_file(self) -> str | None:
        """Get the file this manager actually loaded configuration from.

        Unlike :meth:`get_loaded_config_file`, this never synthesises a target
        from ``ORB_CONFIG_FILE`` or the ``~/.orb`` fallback. It returns the
        construction path only when that file exists on disk (which is what the
        loader read), and ``None`` when configuration came from an in-memory
        dict, environment only, or a path with no backing file. This makes it
        safe to use as an implicit save destination.
        """
        if self._config_file is None:
            return None
        from pathlib import Path

        return self._config_file if Path(self._config_file).exists() else None

    def get_provider_type(self) -> str:
        """Get provider type."""
        return self._ensure_provider_manager().get_provider_type()

    def get_provider_config(self) -> Optional[ProviderConfig]:
        """Get provider configuration."""
        return self._ensure_provider_manager().get_provider_config()

    def get_provider_instance_config(self, provider_name: str):
        """Get configuration for a specific provider instance."""
        return self._ensure_provider_manager().get_provider_instance_config(provider_name)

    def save(self, config_path: str) -> None:
        """Save configuration to file."""
        try:
            raw_config = self._ensure_raw_config()
            with open(config_path, "w") as f:
                json.dump(raw_config, f, indent=2)
            logger.info("Configuration saved to %s", config_path)
        except Exception as e:
            logger.error("Failed to save configuration: %s", e, exc_info=True)
            raise ConfigurationError(f"Failed to save configuration: {e}")

    def get_raw_config(self) -> dict[str, Any]:
        """Get raw configuration dictionary."""
        return self._ensure_raw_config().copy()

    def resolve_file(
        self,
        file_type: str,
        filename: str,
        default_dir: Optional[str] = None,
        explicit_path: Optional[str] = None,
    ) -> str:
        """Resolve a configuration file path with consistent priority:
        1. Explicit path (if provided and contains directory)
        2. Scheduler-provided directory + filename (if file exists)
        3. Default directory + filename

        Args:
            file_type: Type of file ('config', 'template', 'legacy', 'log', 'work', 'events', 'snapshots')
            filename: Name of the file
            default_dir: Default directory (optional, will use resolve_path if not provided)
            explicit_path: Explicit path provided by user (optional)

        Returns:
            Resolved file path
        """
        import os

        # 1. If explicit path provided and contains directory, use it directly
        if explicit_path and os.path.dirname(explicit_path):
            return explicit_path

        # If explicit_path is just a filename, use it as the filename
        if explicit_path and not os.path.dirname(explicit_path):
            filename = explicit_path

        # 2. Try scheduler-provided directory + filename
        scheduler_dir: Optional[str] = None
        try:
            scheduler_dir = self._get_scheduler_directory(file_type)
            if scheduler_dir:
                scheduler_path = os.path.join(scheduler_dir, filename)
                if os.path.exists(scheduler_path):
                    return scheduler_path
        except (OSError, ValueError) as e:
            # Log path resolution failure but continue with fallback
            logger.debug("Failed to resolve scheduler path from %s: %s", scheduler_dir, e)

        # 3. Fall back to default directory + filename
        if default_dir is None:
            # Map file types to path types for resolve_path
            path_type_mapping = {
                "config": "config",
                "template": "config",
                "legacy": "config",
                "log": "log",
                "work": "work",
                "events": "events",
                "snapshots": "snapshots",
            }

            path_type = path_type_mapping.get(file_type, "config")
            default_dir = self.resolve_path(
                path_type, "config" if path_type == "config" else path_type
            )

        fallback_path = os.path.join(default_dir, filename)
        return fallback_path

    def get_scheduler_directory(self, file_type: str) -> Optional[str]:
        """Get directory path for the given file type using platform detection.

        Uses platform_dirs for consistent directory resolution during bootstrap.
        """
        return self._get_scheduler_directory(file_type)

    def _get_scheduler_directory(self, file_type: str) -> Optional[str]:
        """Get directory path for the given file type using platform detection.

        Uses platform_dirs for consistent directory resolution during bootstrap.
        """
        from orb.config.platform_dirs import (
            get_config_location,
            get_logs_location,
            get_work_location,
        )

        logger.debug("[CONFIG_MGR] Getting directory for file_type=%s", file_type)

        if file_type in ["config", "template", "legacy"]:
            result = str(get_config_location())
        elif file_type == "log":
            result = str(get_logs_location())
        elif file_type in ["work", "data"]:
            result = str(get_work_location())
        else:
            result = str(get_work_location())

        logger.debug("[CONFIG_MGR] Resolved directory for file_type=%s: %s", file_type, result)
        return result

    def find_templates_file(self, provider_type: str) -> str:
        """Find templates file with fallback logic.

        Tries in order:
        1. {provider_type}prov_templates.json (e.g., awsprov_templates.json) - for real providers
        2. templates.json (generic) - for default scheduler or fallback

        Args:
            provider_type: Provider type (e.g., "aws") or "default" for default scheduler

        Returns:
            Path to templates file

        Raises:
            FileNotFoundError: If no templates file found
        """
        import os

        # For default scheduler, try templates.json first
        if provider_type == "default":
            candidates = [
                "templates.json",  # Generic (preferred for default)
                "defaultprov_templates.json",  # Provider-specific (unlikely)
            ]
        else:
            candidates = [
                f"{provider_type}prov_templates.json",  # Provider-specific
                "templates.json",  # Generic fallback
            ]

        for filename in candidates:
            try:
                path = self.resolve_file("template", filename)
                if os.path.exists(path):
                    logger.info("Using templates file: %s", filename)
                    return path
            except Exception as e:
                logger.debug("Template file %s not found: %s", filename, e)
                continue

        # No file found - fail with clear error
        template_dir = self._get_scheduler_directory("template") or "config"
        raise FileNotFoundError(
            f"Templates file not found. Tried: {', '.join(candidates)}\n"
            f"In directory: {template_dir}\n"
            f"Run 'orb init' to create configuration and templates"
        )

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return self._cache_manager.get_cache_stats()
