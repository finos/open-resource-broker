"""Provider Strategy Commands - CQRS commands for provider strategy operations.

This module defines commands for managing provider strategies, including
strategy selection, operation execution, health updates, and configuration.
"""

from typing import Any, Optional

from application.dto.base import BaseCommand
from domain.base.operations import Operation


class ExecuteProviderOperationCommand(BaseCommand):
    """Command to execute a provider operation through strategy pattern."""

    operation: Operation
    strategy_override: Optional[str] = None
    retry_count: int = 0
    timeout_seconds: Optional[int] = None

    # Result stored here after execution (CQRS: execute_command returns None)
    result: Optional[dict[str, Any]] = None


class RegisterProviderStrategyCommand(BaseCommand):
    """Command to register a new provider strategy."""

    strategy_name: str
    provider_type: str
    strategy_config: dict[str, Any]
    capabilities: Optional[dict[str, Any]] = None
    priority: int = 0

    # Result stored here after execution (CQRS: execute_command returns None)
    result: Optional[dict[str, Any]] = None


class UpdateProviderHealthCommand(BaseCommand):
    """Command to update provider health status.

    CQRS: Commands should not return data. Results are stored in mutable fields.
    """

    provider_name: str
    health_status: Any
    source: str = "system"
    timestamp: Optional[str] = None

    # Store results for caller to access after command execution
    result: Optional[dict[str, Any]] = None
