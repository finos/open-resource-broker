"""AWS-specific image cache with provider-instance isolation."""

import json
import os
import time
from typing import Any, Dict, Optional

from src.domain.services.image_cache import ImageCache


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
        if image_specification in self._runtime_cache:
            entry = self._runtime_cache[image_specification]
            if time.time() - entry["timestamp"] < self._ttl_seconds:
                return entry["image_id"]
            else:
                # Expired, remove from cache
                del self._runtime_cache[image_specification]
        return None

    def set(self, image_specification: str, image_id: str) -> None:
        """Cache resolved image ID."""
        self._runtime_cache[image_specification] = {
            "image_id": image_id,
            "timestamp": time.time(),
        }
        self._save_cache()

    def clear_expired(self) -> None:
        """Remove expired cache entries."""
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
            except (json.JSONDecodeError, IOError):
                self._runtime_cache = {}

    def _save_cache(self) -> None:
        """Save cache to disk."""
        os.makedirs(os.path.dirname(self._cache_file), exist_ok=True)
        try:
            with open(self._cache_file, "w") as f:
                json.dump(self._runtime_cache, f, indent=2)
        except IOError:
            pass  # Graceful degradation if cache can't be saved
