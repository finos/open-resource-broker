"""System-level commands for administrative operations."""

from typing import Optional

from application.dto.base import BaseCommand

# ============================================================================
# Provider Configuration Management Commands
# ============================================================================


class ReloadProviderConfigCommand(BaseCommand):
    """Command to reload provider configuration from file."""

    config_path: Optional[str] = None
