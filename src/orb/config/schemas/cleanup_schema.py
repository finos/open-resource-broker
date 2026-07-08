"""Re-export shim — CleanupConfig has moved to orb.providers.aws.configuration.cleanup_config.

Note: This shim imports from the AWS provider package.  When the [aws] extra is not
installed, CleanupConfig and CleanupResourcesConfig are set to None.  Phase 2 of the
provider-extras migration will invert this dependency so the models live in core.
"""

try:
    from orb.providers.aws.configuration.cleanup_config import (
        CleanupConfig,
        CleanupResourcesConfig,
    )
except ImportError:
    CleanupConfig = None  # type: ignore[assignment,misc]
    CleanupResourcesConfig = None  # type: ignore[assignment,misc]

__all__ = ["CleanupConfig", "CleanupResourcesConfig"]
