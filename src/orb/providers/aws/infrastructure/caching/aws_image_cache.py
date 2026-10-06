"""AWS-specific image cache with provider-instance isolation."""

import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, Optional

from orb.domain.services.image_cache import ImageCache
from orb.infrastructure.logging.logger import get_logger

_logger = get_logger(__name__)

# Image resolution runs in worker threads and each call builds its own cache
# instance over the same file, so mutation and persistence share one lock.
_cache_lock = threading.RLock()


class AWSImageCache(ImageCache):
    """AWS-specific image cache with provider-instance isolation."""

    def __init__(self, provider_name: str, cache_dir: str, ttl_seconds: int = 3600):
        self._provider_name = provider_name
        self._cache_file = os.path.join(cache_dir, f"image_cache_{provider_name}.json")
        self._ttl_seconds = ttl_seconds
        self._runtime_cache: Dict[str, Dict[str, Any]] = {}
        self._load_cache()

    def get(self, image_specification: str) -> Optional[str]:
        """Get cached image ID for specification."""
        with _cache_lock:
            entry = self._runtime_cache.get(image_specification)
            if entry is not None:
                if time.time() - entry["timestamp"] < self._ttl_seconds:
                    return entry["image_id"]
                # Expired, remove from cache
                del self._runtime_cache[image_specification]
        return None

    def set(self, image_specification: str, image_id: str) -> None:
        """Cache resolved image ID."""
        with _cache_lock:
            self._runtime_cache[image_specification] = {
                "image_id": image_id,
                "timestamp": time.time(),
            }
            self._save_cache()

    def clear_expired(self) -> None:
        """Remove expired cache entries."""
        with _cache_lock:
            current_time = time.time()
            expired_keys = [
                key
                for key, entry in self._runtime_cache.items()
                if current_time - entry["timestamp"] >= self._ttl_seconds
            ]
            for key in expired_keys:
                del self._runtime_cache[key]
            if expired_keys:
                self._save_cache()

    def _load_cache(self) -> None:
        """Load cache from disk."""
        if os.path.exists(self._cache_file):
            try:
                with open(self._cache_file) as f:
                    self._runtime_cache = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                # Corrupt or unreadable cache falls back to an empty cache;
                # log at debug so the reset is not fully silent.
                _logger.debug("Could not load image cache from %s: %s", self._cache_file, e)
                self._runtime_cache = {}

    def _save_cache(self) -> None:
        """Save cache to disk, replacing the file atomically."""
        cache_dir = os.path.dirname(self._cache_file)
        os.makedirs(cache_dir, exist_ok=True)
        tmp_path: Optional[str] = None
        try:
            with _cache_lock:
                fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
                with os.fdopen(fd, "w") as f:
                    json.dump(self._runtime_cache, f, indent=2)
                os.replace(tmp_path, self._cache_file)
                tmp_path = None
        except IOError as e:
            # Graceful degradation if cache can't be saved; log at debug so
            # the failed write is not fully silent.
            _logger.debug("Could not save image cache to %s: %s", self._cache_file, e)
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError as e:
                    # Temp-file cleanup is best effort and must not mask the original outcome.
                    _logger.debug("Could not remove temp cache file %s: %s", tmp_path, e)
