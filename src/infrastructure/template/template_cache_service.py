"""Template cache service with focused responsibilities."""

import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional, Union

from domain.base.ports import LoggingPort

from .dtos import TemplateDTO

# loader_func may return either a plain list or a coroutine
LoaderFunc = Callable[[], Union[list[TemplateDTO], Awaitable[list[TemplateDTO]]]]


class TemplateCacheService(ABC):
    """
    Abstract template cache service interface.

    Follows ISP by providing only core caching operations.
    Focused on single responsibility: template caching.
    """

    @abstractmethod
    async def get_or_load(self, loader_func: LoaderFunc) -> list[TemplateDTO]:
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

    def __init__(self, logger: Optional[LoggingPort] = None) -> None:
        """
        Initialize no-op cache service.

        Args:
            logger: Logging port for service logging
        """
        self._logger = logger

    async def get_or_load(self, loader_func: LoaderFunc) -> list[TemplateDTO]:
        """Load fresh data, no caching."""
        if self._logger:
            self._logger.debug("NoOpTemplateCacheService: Loading fresh templates")
        result = loader_func()
        if hasattr(result, "__await__"):
            return await result  # type: ignore[misc]
        return result  # type: ignore[return-value]

    def get_all(self) -> Optional[list[TemplateDTO]]:
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

    def __init__(self, ttl_seconds: int = 300, logger: Optional[LoggingPort] = None) -> None:
        """
        Initialize TTL cache service.

        Args:
            ttl_seconds: Time-to-live in seconds (default: 5 minutes)
            logger: Logging port for service logging
        """
        self._ttl_seconds = ttl_seconds
        self._logger = logger
        self._cached_templates: Optional[list[TemplateDTO]] = None
        self._cache_time: Optional[datetime] = None
        self._lock = threading.Lock()

    async def get_or_load(self, loader_func: LoaderFunc) -> list[TemplateDTO]:
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
                return self._cached_templates or []

            # Cache miss or expired - load fresh data
            if self._logger:
                self._logger.debug("TTL cache miss: loading fresh templates")

        # Load outside the lock to avoid blocking
        result = loader_func()
        if hasattr(result, "__await__"):
            templates = await result  # type: ignore[misc]
        else:
            templates = result  # type: ignore[assignment]

        with self._lock:
            self._cached_templates = templates  # type: ignore[assignment]
            self._cache_time = datetime.now()

        return templates  # type: ignore[return-value]

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
        logger: Optional[LoggingPort] = None,
    ) -> None:
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
        self._loader_func: Optional[LoaderFunc] = None

    async def get_or_load(self, loader_func: LoaderFunc) -> list[TemplateDTO]:
        """
        Get templates from cache with auto-refresh capability.

        Args:
            loader_func: Function to load templates

        Returns:
            List of templates from cache or freshly loaded
        """
        self._loader_func = loader_func  # type: ignore[assignment]

        templates = await super().get_or_load(loader_func)

        # Schedule refresh if auto-refresh is enabled and cache was loaded
        if self._auto_refresh and self._cache_time:
            self._schedule_refresh()

        return templates

    def _schedule_refresh(self) -> None:
        """Schedule automatic cache refresh."""
        if self._refresh_timer:
            self._refresh_timer.cancel()

        def refresh() -> None:
            """Auto-refresh template cache using loader function."""
            if self._loader_func and self._logger:
                self._logger.debug("Auto-refreshing template cache")
                try:
                    import asyncio

                    result = self._loader_func()
                    if hasattr(result, "__await__"):
                        # Run async function in new event loop for background refresh
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                # Create new thread for async operation if loop is running
                                import threading

                                def async_refresh():
                                    new_loop = asyncio.new_event_loop()
                                    asyncio.set_event_loop(new_loop)
                                    try:
                                        templates = new_loop.run_until_complete(result)  # type: ignore[arg-type]
                                        with self._lock:
                                            self._cached_templates = templates
                                            self._cache_time = datetime.now()
                                    finally:
                                        new_loop.close()

                                threading.Thread(target=async_refresh, daemon=True).start()
                            else:
                                templates = loop.run_until_complete(result)  # type: ignore[arg-type]
                                with self._lock:
                                    self._cached_templates = templates
                                    self._cache_time = datetime.now()
                        except RuntimeError:
                            # No event loop, create one
                            templates = asyncio.run(result)  # type: ignore[arg-type]
                            with self._lock:
                                self._cached_templates = templates
                                self._cache_time = datetime.now()
                    else:
                        with self._lock:
                            self._cached_templates = result  # type: ignore[assignment]
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
    cache_type: str = "noop", logger: Optional[LoggingPort] = None, **kwargs
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
        raise ValueError(f"Unsupported cache type: {cache_type}")
