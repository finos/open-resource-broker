"""
Infrastructure Handler Discovery System.

This module provides the infrastructure implementation for discovering
and registering CQRS handlers. It consumes application-layer handler
registrations and provides concrete discovery mechanisms.

Clean Architecture Compliance:
- Infrastructure depends on Application
- Application does NOT depend on Infrastructure
- Domain is independent

Layer Separation:
- Application: @query_handler, @command_handler decorators
- Infrastructure: Discovery, module scanning, DI registration
"""

import importlib
import json
import os
import pkgutil
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, Optional, Type

from src.application.decorators import (
    get_handler_registry_stats,
    get_registered_command_handlers,
    get_registered_query_handlers,
)
from src.infrastructure.di.container import DIContainer
from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


class HandlerDiscoveryService:
    """
    Infrastructure service for discovering and registering CQRS handlers.

    This service scans application modules to trigger decorator registration,
    then registers discovered handlers with the DI container.

    Improved with caching to improve startup performance by avoiding
    repeated module scanning and handler discovery.
    """

    def __init__(self, container: DIContainer):
        """Initialize the instance."""
        self.container = container

        # Get caching configuration from performance settings
        try:
            from src.config.manager import get_config_manager
            from src.config.schemas.performance_schema import PerformanceConfig

            config_manager = get_config_manager()
            perf_config = config_manager.get_typed(PerformanceConfig)

            self.cache_enabled = perf_config.caching.handler_discovery.enabled
            self.cache_file = (
                self._resolve_cache_path(config_manager) if self.cache_enabled else None
            )

        except Exception as e:
            logger.warning(f"Failed to get caching configuration: {e}")
            # Fallback to default behavior
            self.cache_enabled = True
            self.cache_file = self._resolve_cache_path_fallback()

    def _resolve_cache_path(self, config_manager) -> str:
        """Resolve cache file path using configuration system."""
        try:
            work_dir = config_manager.get_work_dir()
            cache_dir = os.path.join(work_dir, "cache")
            os.makedirs(cache_dir, exist_ok=True)
            return os.path.join(cache_dir, "handler_discovery.json")
        except Exception:
            return self._resolve_cache_path_fallback()

    def _resolve_cache_path_fallback(self) -> str:
        """Fallback cache path resolution."""
        workdir = os.environ.get("HF_PROVIDER_WORKDIR", os.getcwd())
        cache_dir = os.path.join(workdir, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, "handler_discovery.json")

    def discover_and_register_handlers(self, base_package: str = "src.application") -> None:
        """
        Discover all handlers and register them with the DI container.
        Uses caching to improve performance on subsequent runs.

        Args:
            base_package: Base package to scan for handlers
        """
        logger.info(f"Starting handler discovery in package: {base_package}")

        # Try to load from cache first
        cached_result = self._try_load_from_cache(base_package)
        if cached_result:
            logger.info(
                f"Using cached handler discovery ({ cached_result['total_handlers']} handlers)"
            )
            self._register_handlers_from_cache(cached_result["handlers"])
            return

        # Cache miss - perform full discovery
        logger.info("Cache miss - performing full handler discovery")
        start_time = time.time()

        # Discover handlers by importing modules (existing logic)
        self._discover_handlers(base_package)

        # Register discovered handlers with DI container (existing logic)
        self._register_handlers()

        # Cache the results for next time
        discovery_time = time.time() - start_time
        stats = get_handler_registry_stats()
        self._save_to_cache(base_package, stats, discovery_time)

        logger.info(f"Handler discovery complete: {stats} (took {discovery_time:.3f}s)")

    def _discover_handlers(self, base_package: str) -> None:
        """
        Discover handlers by importing all modules in the package.

        This triggers the @query_handler and @command_handler decorators
        to register themselves in the application-layer registry.
        """
        try:
            # Import the base package
            package = importlib.import_module(base_package)
            package_path = Path(package.__file__).parent

            # Walk through all modules in the package
            for module_info in pkgutil.walk_packages([str(package_path)], f"{base_package}."):
                try:
                    # Import the module to trigger decorator registration
                    importlib.import_module(module_info.name)
                    logger.debug(f"Imported module: {module_info.name}")
                except Exception as e:
                    logger.warning(f"Failed to import module {module_info.name}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Handler discovery failed: {e}")
            raise

    def _register_handlers(self) -> None:
        """
        Register all discovered handlers with the DI container.

        Consumes the application-layer handler registries and registers
        handlers as singletons in the infrastructure DI container.
        """
        logger.info("Registering discovered handlers with DI container")

        # Register query handlers
        query_handlers = get_registered_query_handlers()
        for query_type, handler_class in query_handlers.items():
            try:
                # Register handler class for DI container to create instances with
                # proper dependency injection
                self.container.register_singleton(handler_class)
                logger.debug(
                    f"Registered query handler: { handler_class.__name__} for { query_type.__name__}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to register query handler {handler_class.__name__}: {e}")

        # Register command handlers
        command_handlers = get_registered_command_handlers()
        for command_type, handler_class in command_handlers.items():
            try:
                # Register handler class for DI container to create instances with
                # proper dependency injection
                self.container.register_singleton(handler_class)
                logger.debug(
                    f"Registered command handler: { handler_class.__name__} for { command_type.__name__}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to register command handler {handler_class.__name__}: {e}")

        total_registered = len(query_handlers) + len(command_handlers)
        logger.info(
            f"Handler registration complete. Registered {total_registered} handlers")

    def _try_load_from_cache(self, base_package: str) -> Optional[Dict[str, Any]]:
        """Try to load handler discovery results from cache if valid."""
        if not self.cache_enabled or not self.cache_file or not os.path.exists(self.cache_file):
            return None

        try:
            with open(self.cache_file, "r") as f:
                cache_data = json.load(f)

            # Check if cache is for the same base package
            if cache_data.get("base_package") != base_package:
                return None

            # Check if source files have changed since cache was created
            cached_mtimes = cache_data.get("source_mtimes", {})
            current_mtimes = self._get_source_file_mtimes(base_package)

            if cached_mtimes != current_mtimes:
                logger.debug("Cache invalid - source files have changed")
                return None

            logger.debug("Cache is valid - using cached handler discovery")
            return cache_data

        except Exception as e:
            logger.debug(f"Failed to load cache: {e}")
            return None

    def _save_to_cache(
        self, base_package: str, stats: Dict[str, Any], discovery_time: float
    ) -> None:
        """Save handler discovery results to cache."""
        if not self.cache_enabled or not self.cache_file:
            return

        try:
            # Ensure cache directory exists
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)

            # Get current handler information for caching
            query_handlers = get_registered_query_handlers()
            command_handlers = get_registered_command_handlers()

            cache_data = {
                "version": "1.0",
                "cached_at": time.time(),
                "base_package": base_package,
                "source_mtimes": self._get_source_file_mtimes(base_package),
                "discovery_time": discovery_time,
                "stats": stats,
                "total_handlers": stats.get("total_handlers", 0),
                "handlers": {
                    "query_handlers": self._serialize_handlers(query_handlers),
                    "command_handlers": self._serialize_handlers(command_handlers),
                },
            }

            # Atomic write to prevent corruption
            temp_file = f"{self.cache_file}.tmp"
            with open(temp_file, "w") as f:
                json.dump(cache_data, f, indent=2)
            os.rename(temp_file, self.cache_file)

            logger.debug(
                f"Cached handler discovery results ({ stats.get( 'total_handlers', 0)} handlers)"
            )

        except Exception as e:
            logger.debug(f"Failed to save cache: {e}")
            # Continue without caching - not critical for functionality

    def _register_handlers_from_cache(self, cached_handlers: Dict[str, Any]) -> None:
        """Register handlers from cached information."""
        try:
            # Import and register query handlers
            query_handlers_data = cached_handlers.get("query_handlers", {})
            for _query_type_name, handler_info in query_handlers_data.items():
                try:
                    # Import the handler class
                    module = importlib.import_module(handler_info["module"])
                    handler_class = getattr(module, handler_info["class_name"])
                    getattr(module, handler_info["query_type_name"])

                    # Register with DI container
                    self.container.register_singleton(handler_class)
                    logger.debug(
                        f"Registered cached query handler: {handler_class.__name__}")

                except Exception as e:
                    logger.warning(
                        f"Failed to register cached query handler { handler_info.get( 'class_name', 'unknown')}: {e}"
                    )
                    # Fall back to full discovery if cache loading fails
                    self._fallback_to_full_discovery()
                    return

            # Import and register command handlers
            command_handlers_data = cached_handlers.get("command_handlers", {})
            for _command_type_name, handler_info in command_handlers_data.items():
                try:
                    # Import the handler class
                    module = importlib.import_module(handler_info["module"])
                    handler_class = getattr(module, handler_info["class_name"])
                    getattr(module, handler_info["command_type_name"])

                    # Register with DI container
                    self.container.register_singleton(handler_class)
                    logger.debug(
                        f"Registered cached command handler: {handler_class.__name__}")

                except Exception as e:
                    logger.warning(
                        f"Failed to register cached command handler { handler_info.get( 'class_name', 'unknown')}: {e}"
                    )
                    # Fall back to full discovery if cache loading fails
                    self._fallback_to_full_discovery()
                    return

            total_registered = len(query_handlers_data) + len(command_handlers_data)
            logger.info(
                f"Handler registration from cache complete. Registered {total_registered} handlers"
            )

        except Exception as e:
            logger.warning(f"Failed to register handlers from cache: {e}")
            self._fallback_to_full_discovery()

    def _fallback_to_full_discovery(self) -> None:
        """Fall back to full discovery if cache loading fails."""
        logger.info("Falling back to full handler discovery")
        self._discover_handlers("src.application")
        self._register_handlers()

    def _get_source_file_mtimes(self, base_package: str) -> Dict[str, float]:
        """Get modification times of all source files in the package."""
        mtimes = {}

        try:
            # Convert package name to file path
            package_path = base_package.replace(".", "/")

            # Walk through all Python files in the package
            for root, _dirs, files in os.walk(package_path):
                for file in files:
                    if file.endswith(".py"):
                        filepath = os.path.join(root, file)
                        with suppress(OSError):
                            mtimes[filepath] = os.path.getmtime(filepath)
        except Exception as e:
            logger.debug(f"Failed to get source file modification times: {e}")

        return mtimes

    def _serialize_handlers(self, handlers: Dict[Type, Type]) -> Dict[str, Dict[str, str]]:
        """Serialize handler information for caching."""
        serialized = {}

        for handled_type, handler_class in handlers.items():
            try:
                serialized[handled_type.__name__] = {
                    "class_name": handler_class.__name__,
                    "module": handler_class.__module__,
                    "query_type_name": (
                        handled_type.__name__ if "Query" in handled_type.__name__ else None
                    ),
                    "command_type_name": (
                        handled_type.__name__ if "Command" in handled_type.__name__ else None
                    ),
                }
            except Exception as e:
                logger.debug(f"Failed to serialize handler {handler_class}: {e}")
                continue

        return serialized


def create_handler_discovery_service(container: DIContainer) -> HandlerDiscoveryService:
    """
    Create handler discovery service.

    Args:
        container: DI container to register handlers with

    Returns:
        Configured handler discovery service
    """
    return HandlerDiscoveryService(container)
