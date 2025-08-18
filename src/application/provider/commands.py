"""Provider Strategy Commands - CQRS commands for provider strategy operations.

This module defines commands for managing provider strategies, including
strategy selection, operation execution, health updates, and configuration.
"""

from typing import Any, Dict, List, Optional

from application.dto.base import BaseCommand
from providers.base.strategy import (
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    SelectionCriteria,
)


class SelectProviderStrategyCommand(BaseCommand):
    """Command to select optimal provider strategy for an operation."""

    operation_type: ProviderOperationType
    selection_criteria: SelectionCriteria
    context: Optional[Dict[str, Any]] = None


class ExecuteProviderOperationCommand(BaseCommand):
    """Command to execute a provider operation through strategy pattern."""

    operation: ProviderOperation
    strategy_override: Optional[str] = None
    retry_count: int = 0
    timeout_seconds: Optional[int] = None


class RegisterProviderStrategyCommand(BaseCommand):
    """Command to register a new provider strategy."""

    strategy_name: str
    provider_type: str
    strategy_config: Dict[str, Any]
    capabilities: Optional[Dict[str, Any]] = None
    priority: int = 0


class UpdateProviderHealthCommand(BaseCommand):
    """Command to update provider health status."""

    provider_name: str
    health_status: ProviderHealthStatus
    source: str = "system"
    timestamp: Optional[str] = None


class ConfigureProviderStrategyCommand(BaseCommand):
    """Command to configure provider strategy selection policies."""

    default_selection_policy: str
    selection_criteria: Dict[str, Any]
    fallback_strategies: Optional[List[str]] = None
    health_check_interval: int = 300  # 5 minutes default
    circuit_breaker_config: Optional[Dict[str, Any]] = None
