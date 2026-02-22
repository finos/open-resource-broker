"""System-level commands for administrative operations."""

from typing import Any, Optional

from application.dto.base import BaseCommand
from pydantic import ConfigDict

# ============================================================================
# Provider Configuration Management Commands
# ============================================================================


class ReloadProviderConfigCommand(BaseCommand):
    """Command to reload provider configuration from file.

    CQRS: Commands should not return data. Results are stored in mutable fields.
    """

    config_path: Optional[str] = None

    # Mutable result fields for CQRS compliance
    result: Optional[dict[str, Any]] = None

    model_config = ConfigDict(frozen=False)


class RefreshTemplatesCommand(BaseCommand):
    """Command to refresh templates from all sources.

    CQRS: Commands should not return data. Results are stored in mutable fields.
    """

    provider_name: Optional[str] = None

    # Mutable result fields for CQRS compliance
    result: Optional[dict[str, Any]] = None

    model_config = ConfigDict(frozen=False)


class SetConfigurationCommand(BaseCommand):
    """Command to set configuration value.

    CQRS: Commands should not return data. Results are stored in mutable fields.
    """

    key: str
    value: str

    # Mutable result fields for CQRS compliance
    result: Optional[dict[str, Any]] = None

    model_config = ConfigDict(frozen=False)


class TestStorageCommand(BaseCommand):
    """Command to test storage connectivity and functionality.

    CQRS: Commands should not return data. Results are stored in mutable fields.
    Note: This is a pure query operation and should be moved to queries/ in future refactoring.
    """

    storage_type: Optional[str] = None

    # Mutable result fields for CQRS compliance
    result: Optional[dict[str, Any]] = None

    model_config = ConfigDict(frozen=False)


class MCPValidateCommand(BaseCommand):
    """Command to validate MCP server configuration and tools.

    CQRS: Commands should not return data. Results are stored in mutable fields.
    Note: This is a pure query operation and should be moved to queries/ in future refactoring.
    """

    # Mutable result fields for CQRS compliance
    result: Optional[dict[str, Any]] = None

    model_config = ConfigDict(frozen=False)
