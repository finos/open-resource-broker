"""Runtime AMI cache for script execution with optional persistence."""

import json
import logging
import os
import time
from contextlib import suppress
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)


class RuntimeAMICache:
    """
    AMI resolution cache with optional persistence across process boundaries.

    Features:
    - In-memory caching for fast access within process
    - Optional persistent file cache with TTL
    - Failed parameter tracking to avoid retry storms
    - Atomic file operations for safe concurrent access
    - Automatic cleanup of expired entries

    This cache optimizes:
    - Bulk operations (5k templates with same SSM parameter = 1 AWS call)
    - Cross-process caching (subsequent command executions)
    - Development workflows with repeated template operations
    """

    def __init__(self, persistent_file: Optional[str] = None, ttl_minutes: int = 60):
        """
        Initialize AMI cache with optional persistence.

        Args:
            persistent_file: Path to persistent cache file (None = memory only)
            ttl_minutes: Time-to-live for cached entries in minutes
        """
        # In-memory cache
        self._cache: Dict[str, str] = {}  # SSM parameter -> resolved AMI ID
        self._failed: Set[str] = set()  # Failed SSM parameters
        self._cache_metadata: Dict[str, float] = {}  # SSM parameter -> timestamp

        # Persistence settings
        self._persistent_file = persistent_file
        self._ttl_seconds = ttl_minutes * 60

        # Load from persistent cache on startup
        if self._persistent_file:
            self._load_from_persistent_cache()

    def get(self, ssm_parameter: str) -> Optional[str]:
        """
        Get cached AMI ID for SSM parameter, checking TTL.

        Args:
            ssm_parameter: SSM parameter path

        Returns:
            Cached AMI ID if available and not expired, None otherwise
        """
        # Check if entry exists
        if ssm_parameter not in self._cache:
            return None

        # Check TTL if we have metadata and persistent cache is enabled
        if self._persistent_file and ssm_parameter in self._cache_metadata:
            age_seconds = time.time() - self._cache_metadata[ssm_parameter]
            if age_seconds > self._ttl_seconds:
                # Expired - remove from cache
                self._remove_expired_entry(ssm_parameter)
                return None

        return self._cache[ssm_parameter]

    def set(self, ssm_parameter: str, ami_id: str) -> None:
        """
        Cache resolved AMI ID with timestamp and persist if configured.

        Args:
            ssm_parameter: SSM parameter path
            ami_id: Resolved AMI ID
        """
        current_time = time.time()

        # Store in memory with timestamp
        self._cache[ssm_parameter] = ami_id
        if self._persistent_file:
            self._cache_metadata[ssm_parameter] = current_time

        # Persist to file if configured
        if self._persistent_file:
            self._save_to_persistent_cache()

    def mark_failed(self, ssm_parameter: str) -> None:
        """
        Mark SSM parameter as failed and persist if configured.

        Args:
            ssm_parameter: SSM parameter path that failed resolution
        """
        self._failed.add(ssm_parameter)

        # Persist to file if configured
        if self._persistent_file:
            self._save_to_persistent_cache()

    def is_failed(self, ssm_parameter: str) -> bool:
        """
        Check if SSM parameter previously failed resolution.

        Args:
            ssm_parameter: SSM parameter path

        Returns:
            True if parameter previously failed resolution
        """
        return ssm_parameter in self._failed

    def clear(self) -> None:
        """Clear all cached data including persistent cache."""
        self._cache.clear()
        self._failed.clear()
        self._cache_metadata.clear()

        # Clear persistent cache file if configured
        if self._persistent_file and os.path.exists(self._persistent_file):

            with suppress(Exception):
                os.remove(self._persistent_file)

    def get_stats(self) -> Dict[str, int]:
        """
        Get cache statistics including TTL information.

        Returns:
            Dictionary with cache statistics
        """
        expired_count = 0
        if self._persistent_file:
            current_time = time.time()
            for ssm_param in self._cache_metadata:
                age_seconds = current_time - self._cache_metadata[ssm_param]
                if age_seconds > self._ttl_seconds:
                    expired_count += 1

        return {
            "cached_entries": len(self._cache),
            "failed_entries": len(self._failed),
            "total_entries": len(self._cache) + len(self._failed),
            "expired_entries": expired_count,
            "ttl_seconds": self._ttl_seconds,
            "persistent_cache_enabled": self._persistent_file is not None,
        }

    def _load_from_persistent_cache(self) -> None:
        """Load cache from persistent file, filtering expired entries."""
        try:
            if not os.path.exists(self._persistent_file):
                return

            with open(self._persistent_file, "r") as f:
                data = json.load(f)

            current_time = time.time()
            loaded_count = 0
            expired_count = 0

            # Load cache entries with TTL check
            for ssm_param, entry in data.get("cache_entries", {}).items():
                ami_id = entry.get("ami_id")
                timestamp = entry.get("timestamp", 0)

                # Check if entry is still valid
                age_seconds = current_time - timestamp
                if age_seconds <= self._ttl_seconds:
                    self._cache[ssm_param] = ami_id
                    self._cache_metadata[ssm_param] = timestamp
                    loaded_count += 1
                else:
                    expired_count += 1

            # Load failed entries (no TTL for failures within same session)
            self._failed.update(data.get("failed_entries", []))

            # Log cache loading results (only if we loaded something)
            if loaded_count > 0 or expired_count > 0:
                # Note: We can't use logger here as this is called during initialization
                # The CachingAMIResolver will log cache statistics
                pass

        except Exception as e:
            # Silent failure - cache will work without persistence
            logger.debug(f"Failed to load persistent cache: {e}")

    def _save_to_persistent_cache(self) -> None:
        """Save current cache to persistent file using atomic write."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self._persistent_file), exist_ok=True)

            # Prepare data structure
            cache_data = {
                "version": "1.0",
                "created_at": time.time(),
                "ttl_seconds": self._ttl_seconds,
                "cache_entries": {},
                "failed_entries": list(self._failed),
            }

            # Add cache entries with metadata
            for ssm_param, ami_id in self._cache.items():
                cache_data["cache_entries"][ssm_param] = {
                    "ami_id": ami_id,
                    "timestamp": self._cache_metadata.get(ssm_param, time.time()),
                }

            # Atomic write using temp file
            temp_file = f"{self._persistent_file}.tmp"
            with open(temp_file, "w") as f:
                json.dump(cache_data, f, indent=2)

            # Atomic replace
            os.rename(temp_file, self._persistent_file)

        except Exception as e:
            # Silent failure - cache will work without persistence
            logger.debug(f"Failed to save persistent cache: {e}")

    def _remove_expired_entry(self, ssm_parameter: str) -> None:
        """Remove expired entry from cache and metadata."""
        self._cache.pop(ssm_parameter, None)
        self._cache_metadata.pop(ssm_parameter, None)

        # Update persistent cache
        if self._persistent_file:
            self._save_to_persistent_cache()
