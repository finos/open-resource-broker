"""Template cache service with focused responsibilities."""

import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from domain.base.ports import LoggingPort

from .dtos import TemplateDTO


class TemplateCacheService(ABC):
    """
    Abstract template cache service interface.

    Follows ISP by providing only core caching operations.
    Focused on single responsibility: template caching.
    """

    @abstractmethod
    def get_or_load(self, loader_func: Callable[[], List[TemplateDTO]]) -> List[TemplateDTO]:
        """
        Get templates from cache or load using the provided function.

        Args:
            loader_func: Function to load templates if not in cache

        Returns:
            List of TemplateDTO objects
        """

    @abstractmethod
    def invalidate(self) -> None:
        """Invalidate the cache."""

    @abstractmethod
    def is_cached(self) -> bool:
        """Check if templates are currently cached."""


class NoOpTemplateCacheService(TemplateCacheService):
    """
    No-operation cache service that always loads fresh data.

    Useful for development or when caching is disabled.
    """

    def __init__(self, logger: LoggingPort):
        """
        Initialize no-op cache service.

        Args:
            logger: Logging port for service logging
        """
        self._logger = logger

    def get_or_load(self, loader_func: Callable[[], List[TemplateDTO]]) -> List[TemplateDTO]:
        """Load fresh data, no caching."""
        self._logger.debug("NoOpTemplateCacheService: Loading fresh templates")
        return loader_func()

    def get_all(self) -> Optional[List[TemplateDTO]]:
        """Return None as nothing is cached."""
        return None

    def put(self, key: str, template: TemplateDTO) -> None:
        """No-op for putting templates in cache."""

    def invalidate(self) -> None:
        """No-op for invalidation."""

    def is_cached(self) -> bool:
        """Return False as nothing is cached."""
        return False


class TTLTemplateCacheService(TemplateCacheService):
    """
    TTL-based template cache service.

    Caches templates with a time-to-live expiration.
    Follows SRP by focusing only on TTL caching logic.
    """

    def __init__(self, ttl_seconds: int = 300, logger: LoggingPort = None):
        """
        Initialize TTL cache service.

        Args:
            ttl_seconds: Time-to-live in seconds (default: 5 minutes)
            logger: Logging port for service logging
        """
        self._ttl_seconds = ttl_seconds
        self._logger = logger
        self._cached_templates: Optional[List[TemplateDTO]] = None
        self._cache_time: Optional[datetime] = None
        self._lock = threading.Lock()

    def get_or_load(self, loader_func: Callable[[], List[TemplateDTO]]) -> List[TemplateDTO]:
        """
        Get templates from cache or load if expired.

        Args:
            loader_func: Function to load templates if cache is expired

        Returns:
            List of templates from cache or freshly loaded
        """
        with self._lock:
            if self._is_cache_valid():
                if self._logger:
                    self._logger.debug("TTL cache hit: returning cached templates")
                return self._cached_templates

            # Cache miss or expired - load fresh data
            if self._logger:
                self._logger.debug("TTL cache miss: loading fresh templates")

            self._cached_templates = loader_func()
            self._cache_time = datetime.now()

            return self._cached_templates

    def invalidate(self) -> None:
        """Invalidate the cache by clearing cached data."""
        with self._lock:
            self._cached_templates = None
            self._cache_time = None
            if self._logger:
                self._logger.debug("TTL cache invalidated")

    def is_cached(self) -> bool:
        """Check if templates are currently cached and valid."""
        with self._lock:
            return self._is_cache_valid()

    def _is_cache_valid(self) -> bool:
        """
        Check if the current cache is valid (not expired).

        Returns:
            True if cache is valid, False otherwise
        """
        if self._cached_templates is None or self._cache_time is None:
            return False

        age = datetime.now() - self._cache_time
        return age.total_seconds() < self._ttl_seconds

    def get_cache_age(self) -> Optional[timedelta]:
        """
        Get the age of the current cache.

        Returns:
            Cache age as timedelta, None if not cached
        """
        with self._lock:
            if self._cache_time is None:
                return None
            return datetime.now() - self._cache_time

    def get_cache_size(self) -> int:
        """
        Get the number of cached templates.

        Returns:
            Number of cached templates, 0 if not cached
        """
        with self._lock:
            return len(self._cached_templates) if self._cached_templates else 0


class AutoRefreshTemplateCacheService(TTLTemplateCacheService):
    """
    Auto-refresh template cache service.

    Extends TTL cache with automatic background refresh capability.
    Follows SRP by focusing on auto-refresh caching logic.
    """

    def __init__(
        self,
        ttl_seconds: int = 300,
        auto_refresh: bool = False,
        logger: LoggingPort = None,
    ):
        """
        Initialize auto-refresh cache service.

        Args:
            ttl_seconds: Time-to-live in seconds
            auto_refresh: Enable automatic background refresh
            logger: Logging port for service logging
        """
        super().__init__(ttl_seconds, logger)
        self._auto_refresh = auto_refresh
        self._refresh_timer: Optional[threading.Timer] = None
        self._loader_func: Optional[Callable[[], List[TemplateDTO]]] = None

    def get_or_load(self, loader_func: Callable[[], List[TemplateDTO]]) -> List[TemplateDTO]:
        """
        Get templates from cache with auto-refresh capability.

        Args:
            loader_func: Function to load templates

        Returns:
            List of templates from cache or freshly loaded
        """
        self._loader_func = loader_func

        templates = super().get_or_load(loader_func)

        # Schedule refresh if auto-refresh is enabled and cache was loaded
        if self._auto_refresh and self._cache_time:
            self._schedule_refresh()

        return templates

    def _schedule_refresh(self) -> None:
        """Schedule automatic cache refresh."""
        if self._refresh_timer:
            self._refresh_timer.cancel()

        def refresh():
            """Auto-refresh template cache using loader function."""
            if self._loader_func and self._logger:
                self._logger.debug("Auto-refreshing template cache")
                try:
                    with self._lock:
                        self._cached_templates = self._loader_func()
                        self._cache_time = datetime.now()
                except Exception as e:
                    if self._logger:
                        self._logger.error("Auto-refresh failed: %s", e)

        # Schedule refresh at 80% of TTL to ensure fresh data
        refresh_delay = self._ttl_seconds * 0.8
        self._refresh_timer = threading.Timer(refresh_delay, refresh)
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def invalidate(self) -> None:
        """Invalidate cache and cancel any scheduled refresh."""
        if self._refresh_timer:
            self._refresh_timer.cancel()
            self._refresh_timer = None

        super().invalidate()


def create_template_cache_service(
    cache_type: str = "noop", logger: LoggingPort = None, **kwargs
) -> TemplateCacheService:
    """
    Create template cache service.

    Args:
        cache_type: Type of cache ("noop", "ttl", "auto_refresh")
        logger: Logging port for service logging
        **kwargs: Additional arguments for cache configuration

    Returns:
        Template cache service instance

    Raises:
        ValueError: If cache_type is not supported
    """
    if cache_type == "noop":
        return NoOpTemplateCacheService(logger)
    elif cache_type == "ttl":
        return TTLTemplateCacheService(logger=logger, **kwargs)
    elif cache_type == "auto_refresh":
        return AutoRefreshTemplateCacheService(logger=logger, **kwargs)
    else:
        raise ValueError(f"Unsupported cache type: {cache_type}") from e
