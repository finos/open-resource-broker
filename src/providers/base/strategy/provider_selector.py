"""Provider Selector - Strategy selection algorithms and policies.

This module implements various algorithms for selecting provider strategies
based on different criteria such as capabilities, health, performance,
and load balancing requirements.
"""

import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from providers.base.strategy.provider_context import StrategyMetrics
from providers.base.strategy.provider_strategy import (
    ProviderOperation,
    ProviderStrategy,
)


@injectable
class SelectionPolicy(str, Enum):
    """Strategy selection policies."""

    FIRST_AVAILABLE = "first_available"
    ROUND_ROBIN = "round_robin"
    WEIGHTED_ROUND_ROBIN = "weighted_round_robin"
    LEAST_CONNECTIONS = "least_connections"
    FASTEST_RESPONSE = "fastest_response"
    HIGHEST_SUCCESS_RATE = "highest_success_rate"
    CAPABILITY_BASED = "capability_based"
    HEALTH_BASED = "health_based"
    RANDOM = "random"
    CUSTOM = "custom"


@dataclass
class SelectionCriteria:
    """Criteria for strategy selection."""

    required_capabilities: List[str] = None
    min_success_rate: float = 0.0
    max_response_time_ms: float = float("inf")
    require_healthy: bool = True
    exclude_strategies: List[str] = None
    prefer_strategies: List[str] = None
    custom_filter: Optional[Callable[[ProviderStrategy, StrategyMetrics], bool]] = None

    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.required_capabilities is None:
            self.required_capabilities = []
        if self.exclude_strategies is None:
            self.exclude_strategies = []
        if self.prefer_strategies is None:
            self.prefer_strategies = []


@dataclass
class SelectionResult:
    """Result of strategy selection."""

    selected_strategy: Optional[ProviderStrategy]
    selection_reason: str
    alternatives: List[ProviderStrategy] = None
    selection_time_ms: float = 0.0

    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.alternatives is None:
            self.alternatives = []

    @property
    def success(self) -> bool:
        """Check if selection was successful."""
        return self.selected_strategy is not None


@injectable
class ProviderSelector(ABC):
    """
    Abstract base class for provider strategy selectors.

    This class defines the interface for strategy selection algorithms
    that can be used by the ProviderContext to choose the most appropriate
    strategy for a given operation.
    """

    def __init__(self, logger: LoggingPort) -> None:
        """Initialize the selector."""
        self._logger = logger

    @abstractmethod
    def select_strategy(
        self,
        strategies: Dict[str, ProviderStrategy],
        metrics: Dict[str, StrategyMetrics],
        operation: ProviderOperation,
        criteria: SelectionCriteria = None,
    ) -> SelectionResult:
        """
        Select the best strategy for the given operation.

        Args:
            strategies: Available strategies
            metrics: Strategy performance metrics
            operation: Operation to be executed
            criteria: Selection criteria

        Returns:
            Selection result with chosen strategy
        """


@injectable
class FirstAvailableSelector(ProviderSelector):
    """Selects the first available and healthy strategy."""

    def select_strategy(
        self,
        strategies: Dict[str, ProviderStrategy],
        metrics: Dict[str, StrategyMetrics],
        operation: ProviderOperation,
        criteria: SelectionCriteria = None,
    ) -> SelectionResult:
        """Select the first available strategy."""
        start_time = time.time()
        criteria = criteria or SelectionCriteria()

        for strategy_type, strategy in strategies.items():
            if self._is_strategy_suitable(strategy, metrics.get(strategy_type), criteria):
                selection_time = (time.time() - start_time) * 1000
                return SelectionResult(
                    selected_strategy=strategy,
                    selection_reason=f"First available strategy: {strategy_type}",
                    selection_time_ms=selection_time,
                )

        selection_time = (time.time() - start_time) * 1000
        return SelectionResult(
            selected_strategy=None,
            selection_reason="No suitable strategy found",
            selection_time_ms=selection_time,
        )

    def _is_strategy_suitable(
        self,
        strategy: ProviderStrategy,
        metrics: Optional[StrategyMetrics],
        criteria: SelectionCriteria,
    ) -> bool:
        """Check if strategy meets the criteria."""
        # Check exclusions
        if strategy.provider_type in criteria.exclude_strategies:
            return False

        # Check health if required
        if criteria.require_healthy:
            try:
                health = strategy.check_health()
                if not health.is_healthy:
                    return False
            except Exception:
                return False

        # Check capabilities
        if criteria.required_capabilities:
            try:
                capabilities = strategy.get_capabilities()
                for required_cap in criteria.required_capabilities:
                    if not capabilities.get_feature(required_cap, False):
                        return False
            except Exception:
                return False

        # Check metrics if available
        if metrics:
            if metrics.success_rate < criteria.min_success_rate:
                return False
            if metrics.average_response_time_ms > criteria.max_response_time_ms:
                return False

        # Check custom filter
        if criteria.custom_filter and not criteria.custom_filter(strategy, metrics):
            return False

        return True


@injectable
class RoundRobinSelector(ProviderSelector):
    """Selects strategies in round-robin fashion."""

    def __init__(self, logger: LoggingPort) -> None:
        """Initialize round-robin selector."""
        super().__init__(logger)
        self._last_selected_index = -1

    def select_strategy(
        self,
        strategies: Dict[str, ProviderStrategy],
        metrics: Dict[str, StrategyMetrics],
        operation: ProviderOperation,
        criteria: SelectionCriteria = None,
    ) -> SelectionResult:
        """Select strategy using round-robin algorithm."""
        start_time = time.time()
        criteria = criteria or SelectionCriteria()

        # Filter suitable strategies
        suitable_strategies = []
        for strategy_type, strategy in strategies.items():
            if self._is_strategy_suitable(strategy, metrics.get(strategy_type), criteria):
                suitable_strategies.append((strategy_type, strategy))

        if not suitable_strategies:
            selection_time = (time.time() - start_time) * 1000
            return SelectionResult(
                selected_strategy=None,
                selection_reason="No suitable strategies found",
                selection_time_ms=selection_time,
            )

        # Round-robin selection
        self._last_selected_index = (self._last_selected_index + 1) % len(suitable_strategies)
        selected_type, selected_strategy = suitable_strategies[self._last_selected_index]

        selection_time = (time.time() - start_time) * 1000
        return SelectionResult(
            selected_strategy=selected_strategy,
            selection_reason=f"Round-robin selection: {selected_type} (index {self._last_selected_index})",
            alternatives=[s[1] for s in suitable_strategies if s[1] != selected_strategy],
            selection_time_ms=selection_time,
        )

    def _is_strategy_suitable(
        self,
        strategy: ProviderStrategy,
        metrics: Optional[StrategyMetrics],
        criteria: SelectionCriteria,
    ) -> bool:
        """Reuse the suitability check from FirstAvailableSelector."""
        return FirstAvailableSelector._is_strategy_suitable(self, strategy, metrics, criteria)


@injectable
class PerformanceBasedSelector(ProviderSelector):
    """Selects strategies based on performance metrics."""

    def select_strategy(
        self,
        strategies: Dict[str, ProviderStrategy],
        metrics: Dict[str, StrategyMetrics],
        operation: ProviderOperation,
        criteria: SelectionCriteria = None,
    ) -> SelectionResult:
        """Select strategy with best performance metrics."""
        start_time = time.time()
        criteria = criteria or SelectionCriteria()

        # Filter and score suitable strategies
        scored_strategies = []
        for strategy_type, strategy in strategies.items():
            strategy_metrics = metrics.get(strategy_type)
            if self._is_strategy_suitable(strategy, strategy_metrics, criteria):
                score = self._calculate_performance_score(strategy_metrics)
                scored_strategies.append((score, strategy_type, strategy))

        if not scored_strategies:
            selection_time = (time.time() - start_time) * 1000
            return SelectionResult(
                selected_strategy=None,
                selection_reason="No suitable strategies found",
                selection_time_ms=selection_time,
            )

        # Sort by score (higher is better)
        scored_strategies.sort(key=lambda x: x[0], reverse=True)
        best_score, best_type, best_strategy = scored_strategies[0]

        selection_time = (time.time() - start_time) * 1000
        return SelectionResult(
            selected_strategy=best_strategy,
            selection_reason=f"Best performance: {best_type} (score: {best_score:.2f})",
            alternatives=[s[2] for s in scored_strategies[1:]],
            selection_time_ms=selection_time,
        )

    def _calculate_performance_score(self, metrics: Optional[StrategyMetrics]) -> float:
        """Calculate performance score for a strategy."""
        if not metrics or metrics.total_operations == 0:
            return 0.0

        # Weighted score based on success rate and response time
        success_weight = 0.7
        speed_weight = 0.3

        success_score = metrics.success_rate / 100.0  # Normalize to 0-1

        # Inverse response time score (faster = better)
        # Use 1000ms as baseline - strategies faster than this get bonus
        baseline_response_time = 1000.0
        if metrics.average_response_time_ms > 0:
            speed_score = min(1.0, baseline_response_time / metrics.average_response_time_ms)
        else:
            speed_score = 1.0

        return (success_score * success_weight) + (speed_score * speed_weight)

    def _is_strategy_suitable(
        self,
        strategy: ProviderStrategy,
        metrics: Optional[StrategyMetrics],
        criteria: SelectionCriteria,
    ) -> bool:
        """Reuse the suitability check from FirstAvailableSelector."""
        return FirstAvailableSelector._is_strategy_suitable(self, strategy, metrics, criteria)


@injectable
class RandomSelector(ProviderSelector):
    """Selects strategies randomly from suitable candidates."""

    def select_strategy(
        self,
        strategies: Dict[str, ProviderStrategy],
        metrics: Dict[str, StrategyMetrics],
        operation: ProviderOperation,
        criteria: SelectionCriteria = None,
    ) -> SelectionResult:
        """Select strategy randomly."""
        start_time = time.time()
        criteria = criteria or SelectionCriteria()

        # Filter suitable strategies
        suitable_strategies = []
        for strategy_type, strategy in strategies.items():
            if self._is_strategy_suitable(strategy, metrics.get(strategy_type), criteria):
                suitable_strategies.append((strategy_type, strategy))

        if not suitable_strategies:
            selection_time = (time.time() - start_time) * 1000
            return SelectionResult(
                selected_strategy=None,
                selection_reason="No suitable strategies found",
                selection_time_ms=selection_time,
            )

        # Random selection using cryptographically secure randomness
        selected_type, selected_strategy = secrets.choice(suitable_strategies)

        selection_time = (time.time() - start_time) * 1000
        return SelectionResult(
            selected_strategy=selected_strategy,
            selection_reason=f"Random selection: {selected_type}",
            alternatives=[s[1] for s in suitable_strategies if s[1] != selected_strategy],
            selection_time_ms=selection_time,
        )

    def _is_strategy_suitable(
        self,
        strategy: ProviderStrategy,
        metrics: Optional[StrategyMetrics],
        criteria: SelectionCriteria,
    ) -> bool:
        """Reuse the suitability check from FirstAvailableSelector."""
        return FirstAvailableSelector._is_strategy_suitable(self, strategy, metrics, criteria)


class SelectorFactory:
    """Factory for creating provider selectors."""

    _selectors = {
        SelectionPolicy.FIRST_AVAILABLE: FirstAvailableSelector,
        SelectionPolicy.ROUND_ROBIN: RoundRobinSelector,
        SelectionPolicy.FASTEST_RESPONSE: PerformanceBasedSelector,
        SelectionPolicy.HIGHEST_SUCCESS_RATE: PerformanceBasedSelector,
        SelectionPolicy.RANDOM: RandomSelector,
    }

    @classmethod
    def create_selector(cls, policy: SelectionPolicy, logger=None) -> ProviderSelector:
        """
        Create a selector for the given policy.

        Args:
            policy: Selection policy
            logger: Optional logger

        Returns:
            Provider selector instance

        Raises:
            ValueError: If policy is not supported
        """
        if policy not in cls._selectors:
            raise ValueError(f"Unsupported selection policy: {policy}")

        selector_class = cls._selectors[policy]
        return selector_class(logger)

    @classmethod
    def register_selector(cls, policy: SelectionPolicy, selector_class: type) -> None:
        """Register a custom selector class."""
        if not issubclass(selector_class, ProviderSelector):
            raise ValueError("Selector class must inherit from ProviderSelector")

        cls._selectors[policy] = selector_class

    @classmethod
    def get_supported_policies(cls) -> List[SelectionPolicy]:
        """Get list of supported selection policies."""
        return list(cls._selectors.keys())
