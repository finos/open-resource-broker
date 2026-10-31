"""Criteria used when selecting a provider strategy."""

from dataclasses import dataclass
from typing import Callable, Optional

from orb.providers.base.strategy.provider_strategy import ProviderStrategy


@dataclass
class SelectionCriteria:
    """Criteria for strategy selection."""

    required_capabilities: Optional[list[str]] = None
    min_success_rate: float = 0.0
    max_response_time_ms: float = float("inf")
    require_healthy: bool = True
    exclude_strategies: Optional[list[str]] = None
    prefer_strategies: Optional[list[str]] = None
    custom_filter: Optional[Callable[[ProviderStrategy, dict], bool]] = None

    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.required_capabilities is None:
            self.required_capabilities = []
        if self.exclude_strategies is None:
            self.exclude_strategies = []
        if self.prefer_strategies is None:
            self.prefer_strategies = []
