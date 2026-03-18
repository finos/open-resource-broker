"""Re-export shim — CleanupConfig has moved to orb.providers.aws.configuration.cleanup_config."""

from orb.providers.aws.configuration.cleanup_config import (
    CleanupConfig,
    CleanupResourcesConfig,
)

__all__ = ["CleanupConfig", "CleanupResourcesConfig"]
