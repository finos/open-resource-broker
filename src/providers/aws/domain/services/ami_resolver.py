"""AWS AMI Resolution service for AWS provider."""

import json
import os
import time
import hashlib
from typing import Dict, Any, Optional
from domain.template.image_resolver import ImageResolver


class AWSAMIResolver(ImageResolver):
    """
    AWS-specific implementation for resolving AMI IDs from various formats.

    This service handles AWS-specific AMI resolution including:
    - Direct AMI IDs (ami-xxxxxxxx)
    - SSM parameter paths (/aws/service/ami-amazon-linux-latest/...)
    - Custom AMI aliases
    - Runtime caching via injected cache service
    - Persistent cache file support
    - Sophisticated fallback mechanisms
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, cache_service=None):
        """
        Initialize AWS AMI resolver with optional cache service.

        Args:
            config: Configuration dictionary with cache settings
            cache_service: Injected cache service from infrastructure layer
        """
        self._config = config or {}

        # Cache configuration
        cache_config = self._config.get("cache", {})
        self._cache_enabled = cache_config.get("enabled", True)

        # Use injected cache service
        self._cache = cache_service if self._cache_enabled else None

        # Persistent cache configuration
        self._persistent_cache_enabled = cache_config.get("persistent", False)
        self._cache_file_path = cache_config.get("file_path", "/tmp/ami_cache.json")
        self._allow_stale_fallback = cache_config.get("allow_stale_fallback", True)
        self._max_stale_age_seconds = cache_config.get("max_stale_age_seconds", 86400)

        # Load persistent cache if enabled
        if self._cache_enabled and self._persistent_cache_enabled and self._cache:
            self._load_persistent_cache()

    def __del__(self):
        """Save persistent cache on destruction."""
        if (
            self._cache_enabled
            and self._persistent_cache_enabled
            and hasattr(self, "_cache")
            and self._cache
        ):
            self._save_persistent_cache()

    def resolve_image_id(self, image_reference: str) -> str:
        """
        Resolve AMI reference to actual AMI ID with caching.

        Args:
            image_reference: AMI ID, alias, or SSM parameter path

        Returns:
            Resolved AMI ID

        Raises:
            ValueError: If AMI cannot be resolved
        """
        if not image_reference:
            raise ValueError("Image reference cannot be empty")

        # Direct AMI ID - return as-is
        if image_reference.startswith("ami-"):
            return image_reference

        # Generate cache key for non-AMI references
        cache_key = self._generate_cache_key(image_reference)

        # Try cache first if enabled
        if self._cache_enabled and self._cache:
            cached_result = self._cache.get(cache_key)
            if cached_result:
                return cached_result

            # Skip if recently failed
            if self._cache.is_failed(cache_key):
                return image_reference

        try:
            # Resolve the reference
            if image_reference.startswith("/aws/service/"):
                resolved_ami = self._resolve_ssm_parameter(image_reference)
            elif self._is_custom_alias(image_reference):
                resolved_ami = self._resolve_custom_alias(image_reference)
            else:
                # If we can't resolve it, return as-is
                return image_reference

            # Cache successful resolution
            if self._cache_enabled and self._cache:
                self._cache.set(cache_key, resolved_ami)

            return resolved_ami

        except Exception as e:
            # Mark as failed in cache
            if self._cache_enabled and self._cache:
                self._cache.mark_failed(cache_key)

            # Try stale cache fallback
            if self._allow_stale_fallback and self._cache_enabled and self._cache:
                stale_result = self._cache.get_stale(cache_key)
                if stale_result:
                    return stale_result

            # For unknown aliases, return original reference instead of raising error
            if self._is_custom_alias(image_reference) and "Unknown AMI alias" in str(e):
                return image_reference

            # No fallback available, re-raise error
            raise ValueError(f"Failed to resolve image reference {image_reference}: {e!s}")

    def supports_reference_format(self, image_reference: str) -> bool:
        """
        Check if this resolver supports the given image reference format.

        Args:
            image_reference: Image reference to check

        Returns:
            True if this resolver can handle the reference format
        """
        if not image_reference:
            return False

        # Support direct AMI IDs, SSM parameters, and custom aliases
        return (
            image_reference.startswith("ami-")
            or image_reference.startswith("/aws/service/")
            or self._is_custom_alias(image_reference)
        )

    def _resolve_ssm_parameter(self, ssm_path: str) -> str:
        """
        Resolve SSM parameter to AMI ID.

        Args:
            ssm_path: SSM parameter path

        Returns:
            Resolved AMI ID

        Raises:
            ValueError: If SSM parameter cannot be resolved
        """
        try:
            import boto3
            from botocore.exceptions import ClientError

            ssm_client = boto3.client("ssm")
            response = ssm_client.get_parameter(Name=ssm_path)
            ami_id = str(response["Parameter"]["Value"])

            if not ami_id.startswith("ami-"):
                raise ValueError(
                    f"SSM parameter {ssm_path} did not return a valid AMI ID: {ami_id}"
                )

            return ami_id

        except ClientError as e:
            raise ValueError(f"Failed to resolve SSM parameter {ssm_path}: {e}")
        except ImportError:
            raise ValueError("boto3 is required for SSM parameter resolution")
        except Exception as e:
            raise ValueError(f"Unexpected error resolving SSM parameter {ssm_path}: {e}")

    def _is_custom_alias(self, reference: str) -> bool:
        """
        Check if reference is a custom alias.

        Args:
            reference: Image reference

        Returns:
            True if it's a custom alias format
        """
        # Custom aliases are typically short names without special prefixes
        return (
            len(reference) < 50
            and not reference.startswith(("ami-", "/aws/", "arn:"))
            and reference.replace("-", "").replace("_", "").isalnum()
        )

    def _resolve_custom_alias(self, alias: str) -> str:
        """
        Resolve custom alias to AMI ID.

        Args:
            alias: Custom alias

        Returns:
            Resolved AMI ID

        Raises:
            ValueError: If alias cannot be resolved
        """
        # Common alias mappings - in a real implementation, this might come from
        # configuration
        alias_mappings = {
            "amazon-linux-2": "/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2",
            "ubuntu-20.04": "/aws/service/canonical/ubuntu/server/20.04/stable/current/amd64/hvm/ebs-gp2/ami-id",
            "ubuntu-22.04": "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id",
            "windows-2019": "/aws/service/ami-windows-latest/Windows_Server-2019-English-Full-Base",
            "windows-2022": "/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base",
        }

        if alias in alias_mappings:
            # Recursively resolve the SSM parameter
            return self.resolve_image_id(alias_mappings[alias])

        raise ValueError(f"Unknown AMI alias: {alias}")

    def _generate_cache_key(self, reference: str) -> str:
        """Generate consistent cache key from reference."""
        return hashlib.md5(reference.encode()).hexdigest()

    def _load_persistent_cache(self) -> None:
        """Load cache from persistent file."""
        try:
            if os.path.exists(self._cache_file_path):
                with open(self._cache_file_path) as f:
                    cache_data = json.load(f)
                    # Load cache entries
                    for key, entry in cache_data.get("cache", {}).items():
                        self._cache.set(key, entry["data"])

                    # Load failed entries
                    for key in cache_data.get("failed", []):
                        self._cache.mark_failed(key)
        except Exception:
            # Ignore errors loading persistent cache
            pass

    def _save_persistent_cache(self) -> None:
        """Save cache to persistent file."""
        try:
            os.makedirs(os.path.dirname(self._cache_file_path), exist_ok=True)
            cache_data = {
                "cache": {},
                "failed": [],
                "saved_at": time.time(),
            }
            # Note: This is a simplified version since we can't access internal cache state
            # In a full implementation, the cache service would provide export/import methods
            with open(self._cache_file_path, "w") as f:
                json.dump(cache_data, f, indent=2)
        except Exception:
            # Ignore errors saving persistent cache
            pass

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        if not self._cache_enabled or not self._cache:
            return {"cache_enabled": False}

        stats = self._cache.get_stats()
        stats["cache_enabled"] = True
        stats["persistent_cache_enabled"] = self._persistent_cache_enabled
        stats["cache_file_path"] = self._cache_file_path if self._persistent_cache_enabled else None
        return stats

    def clear_cache(self) -> None:
        """Clear the cache."""
        if self._cache_enabled and self._cache:
            self._cache.clear()

    def remove_expired_entries(self) -> int:
        """Remove expired cache entries and return count."""
        if not self._cache_enabled or not self._cache:
            return 0
        return self._cache.clear_expired()
