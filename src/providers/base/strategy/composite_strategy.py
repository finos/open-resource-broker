"""Composite strategy pattern implementation for provider operations.

Composite Provider Strategy - Multi-provider composition and orchestration.

This module implements the Composite pattern for provider strategies,
enabling complex multi-provider operations, load distribution, and
coordinated resource management across different cloud providers.
"""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from infrastructure.interfaces.provider import BaseProviderConfig
from providers.base.strategy.provider_strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderResult,
    ProviderStrategy,
)


@injectable
class CompositionMode(str, Enum):
    """Modes for composite strategy execution."""

    PARALLEL = "parallel"  # Execute on all strategies simultaneously
    SEQUENTIAL = "sequential"  # Execute on strategies one by one
    LOAD_BALANCED = "load_balanced"  # Distribute load across strategies
    AGGREGATED = "aggregated"  # Combine results from multiple strategies
    REDUNDANT = "redundant"  # Execute on multiple for redundancy


@injectable
class AggregationPolicy(str, Enum):
    """Policies for aggregating results from multiple strategies."""

    MERGE_ALL = "merge_all"  # Merge all successful results
    FIRST_SUCCESS = "first_success"  # Return first successful result
    MAJORITY_WINS = "majority_wins"  # Return result agreed by majority
    BEST_PERFORMANCE = "best_performance"  # Return result from best performing strategy


@dataclass
class CompositionConfig:
    """Configuration for composite strategy behavior."""

    mode: CompositionMode = CompositionMode.PARALLEL
    aggregation_policy: AggregationPolicy = AggregationPolicy.MERGE_ALL
    max_concurrent_operations: int = 5
    timeout_seconds: float = 30.0
    require_all_success: bool = False
    min_success_count: int = 1
    failure_threshold: float = 0.5  # Fail if more than 50% of strategies fail

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if self.max_concurrent_operations < 1:
            raise ValueError("max_concurrent_operations must be at least 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.min_success_count < 1:
            raise ValueError("min_success_count must be at least 1")
        if not 0 <= self.failure_threshold <= 1:
            raise ValueError("failure_threshold must be between 0 and 1")


@dataclass
class StrategyExecutionResult:
    """Result of executing an operation on a single strategy."""

    strategy_type: str
    result: ProviderResult
    execution_time_ms: float
    success: bool
    error: Optional[Exception] = None


@injectable
class CompositeProviderStrategy(ProviderStrategy):
    """
    Composite provider strategy that orchestrates multiple provider strategies.

    This class implements the Composite pattern to enable complex multi-provider
    operations such as:
    - Load balancing across multiple providers
    - Parallel execution for performance
    - Redundant execution for reliability
    - Result aggregation and consensus
    - Cross-provider resource coordination

    Features:
    - Multiple composition modes (parallel, sequential, load-balanced)
    - Flexible result aggregation policies
    - Timeout and error handling
    - Performance monitoring and optimization
    - Thread-safe concurrent execution
    """

    def __init__(
        self,
        logger: LoggingPort,
        strategies: List[ProviderStrategy],
        config: CompositionConfig = None,
    ) -> None:
        """
        Initialize composite provider strategy.

        Args:
            strategies: List of provider strategies to compose
            config: Composition configuration
            logger: Optional logger instance

        Raises:
            ValueError: If strategies list is empty or invalid
        """
        if not strategies:
            raise ValueError("At least one strategy is required for composition")

        # Create a dummy config for the parent class

        dummy_config = BaseProviderConfig(provider_type="composite")
        super().__init__(dummy_config)

        self._strategies = {strategy.provider_type: strategy for strategy in strategies}
        self._config = config or CompositionConfig()
        self._logger = logger
        self._executor = ThreadPoolExecutor(max_workers=self._config.max_concurrent_operations)
        self._strategy_weights: Dict[str, float] = {}

        # Initialize equal weights for load balancing
        weight = 1.0 / len(self._strategies)
        for strategy_type in self._strategies:
            self._strategy_weights[strategy_type] = weight

    @property
    def provider_type(self) -> str:
        """Get the provider type identifier."""
        strategy_types = sorted(self._strategies.keys())
        return f"composite({'+'.join(strategy_types)})"

    @property
    def composed_strategies(self) -> Dict[str, ProviderStrategy]:
        """Get the composed strategies."""
        return self._strategies.copy()

    @property
    def composition_config(self) -> CompositionConfig:
        """Get the composition configuration."""
        return self._config

    def add_strategy(self, strategy: ProviderStrategy, weight: float = None) -> None:
        """
        Add a new strategy to the composition.

        Args:
            strategy: Provider strategy to add
            weight: Optional weight for load balancing (auto-calculated if None)
        """
        strategy_type = strategy.provider_type

        if strategy_type in self._strategies:
            self._self._logger.warning("Strategy %s already exists, replacing", strategy_type)

        self._strategies[strategy_type] = strategy

        # Calculate weight
        if weight is None:
            weight = 1.0 / len(self._strategies)
            # Rebalance existing weights
            for existing_type in self._strategy_weights:
                self._strategy_weights[existing_type] = weight

        self._strategy_weights[strategy_type] = weight
        self._self._logger.info("Added strategy %s with weight %s", strategy_type, weight)

    def remove_strategy(self, strategy_type: str) -> bool:
        """
        Remove a strategy from the composition.

        Args:
            strategy_type: Type of strategy to remove

        Returns:
            True if strategy was removed, False if not found
        """
        if strategy_type not in self._strategies:
            return False

        strategy = self._strategies[strategy_type]

        # Clean up strategy
        try:
            strategy.cleanup()
        except Exception as e:
            self._self._logger.warning("Error cleaning up strategy %s: %s", strategy_type, e)

        # Remove from composition
        del self._strategies[strategy_type]
        del self._strategy_weights[strategy_type]

        # Rebalance weights
        if self._strategies:
            weight = 1.0 / len(self._strategies)
            for remaining_type in self._strategy_weights:
                self._strategy_weights[remaining_type] = weight

        self._self._logger.info("Removed strategy %s", strategy_type)
        return True

    def set_strategy_weight(self, strategy_type: str, weight: float) -> bool:
        """
        Set the weight for a specific strategy in load balancing.

        Args:
            strategy_type: Type of strategy
            weight: Weight value (0.0 to 1.0)

        Returns:
            True if weight was set, False if strategy not found
        """
        if strategy_type not in self._strategies:
            return False

        if not 0.0 <= weight <= 1.0:
            raise ValueError("Weight must be between 0.0 and 1.0")

        self._strategy_weights[strategy_type] = weight
        self._self._logger.debug("Set weight for %s: %s", strategy_type, weight)
        return True

    def initialize(self) -> bool:
        """
        Initialize all composed strategies.

        Returns:
            True if at least min_success_count strategies initialize successfully
        """
        if self._initialized:
            return True

        self._self._logger.info(
            "Initializing composite strategy with %s strategies", len(self._strategies)
        )

        success_count = 0
        total_count = len(self._strategies)

        for strategy_type, strategy in self._strategies.items():
            try:
                if not strategy.is_initialized:
                    if strategy.initialize():
                        success_count += 1
                        self._self._logger.info("Initialized strategy: %s", strategy_type)
                    else:
                        self._self._logger.error("Failed to initialize strategy: %s", strategy_type)
                else:
                    success_count += 1
                    self._self._logger.debug("Strategy already initialized: %s", strategy_type)

            except Exception as e:
                self._self._logger.error("Error initializing strategy %s: %s", strategy_type, e)

        # Check if we have enough successful initializations
        min_required = max(1, self._config.min_success_count)
        self._initialized = success_count >= min_required

        if self._initialized:
            self._self._logger.info(
                "Composite strategy initialized: %s/%s strategies ready", success_count, total_count
            )
        else:
            self._self._logger.error(
                "Composite strategy initialization failed: only %s/%s strategies ready, need %s",
                success_count,
                total_count,
                min_required,
            )

        return self._initialized

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """
        Execute operation using the configured composition mode.

        Args:
            operation: The operation to execute

        Returns:
            Aggregated result from the composed strategies
        """
        if not self._initialized:
            return ProviderResult.error_result(
                "Composite strategy not initialized", "NOT_INITIALIZED"
            )

        start_time = time.time()

        try:
            # Filter strategies that support the operation
            capable_strategies = self._filter_capable_strategies(operation)

            if not capable_strategies:
                return ProviderResult.error_result(
                    f"No strategies support operation {operation.operation_type}",
                    "NO_CAPABLE_STRATEGIES",
                )

            # Execute based on composition mode
            if self._config.mode == CompositionMode.PARALLEL:
                execution_results = self._execute_parallel(capable_strategies, operation)
            elif self._config.mode == CompositionMode.SEQUENTIAL:
                execution_results = await self._execute_sequential(capable_strategies, operation)
            elif self._config.mode == CompositionMode.LOAD_BALANCED:
                execution_results = await self._execute_load_balanced(capable_strategies, operation)
            else:
                execution_results = self._execute_parallel(capable_strategies, operation)

            # Aggregate results
            final_result = self._aggregate_results(execution_results, operation)

            # Add execution metadata
            total_time_ms = (time.time() - start_time) * 1000
            final_result.metadata.update(
                {
                    "composition_mode": self._config.mode.value,
                    "total_execution_time_ms": total_time_ms,
                    "strategies_executed": len(execution_results),
                    "successful_strategies": len([r for r in execution_results if r.success]),
                }
            )

            return final_result

        except Exception as e:
            total_time_ms = (time.time() - start_time) * 1000
            self._self._logger.error(
                "Composite operation %s failed: %s", operation.operation_type, e
            )
            return ProviderResult.error_result(
                f"Composite operation failed: {str(e)}",
                "COMPOSITE_EXECUTION_ERROR",
                {"total_execution_time_ms": total_time_ms},
            )

    def _filter_capable_strategies(
        self, operation: ProviderOperation
    ) -> Dict[str, ProviderStrategy]:
        """Filter strategies that can handle the operation."""
        capable = {}

        for strategy_type, strategy in self._strategies.items():
            try:
                capabilities = strategy.get_capabilities()
                if capabilities.supports_operation(operation.operation_type):
                    capable[strategy_type] = strategy
            except Exception as e:
                self._self._logger.warning(
                    "Error checking capabilities for %s: %s", strategy_type, e
                )

        return capable

    def _execute_parallel(
        self, strategies: Dict[str, ProviderStrategy], operation: ProviderOperation
    ) -> List[StrategyExecutionResult]:
        """Execute operation on all strategies in parallel."""
        futures = {}

        for strategy_type, strategy in strategies.items():
            future = self._executor.submit(
                self._execute_single_strategy_sync, strategy_type, strategy, operation
            )
            futures[future] = strategy_type

        results = []
        for future in as_completed(futures, timeout=self._config.timeout_seconds):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                strategy_type = futures[future]
                results.append(
                    StrategyExecutionResult(
                        strategy_type=strategy_type,
                        result=ProviderResult.error_result(
                            f"Execution failed: {str(e)}", "EXECUTION_ERROR"
                        ),
                        execution_time_ms=0.0,
                        success=False,
                        error=e,
                    )
                )

        return results

    async def _execute_sequential(
        self, strategies: Dict[str, ProviderStrategy], operation: ProviderOperation
    ) -> List[StrategyExecutionResult]:
        """Execute operation on strategies sequentially."""
        results = []

        for strategy_type, strategy in strategies.items():
            result = await self._execute_single_strategy(strategy_type, strategy, operation)
            results.append(result)

            # Stop on first success if configured
            if (
                self._config.aggregation_policy == AggregationPolicy.FIRST_SUCCESS
                and result.success
            ):
                break

        return results

    async def _execute_load_balanced(
        self, strategies: Dict[str, ProviderStrategy], operation: ProviderOperation
    ) -> List[StrategyExecutionResult]:
        """Execute operation using load balancing."""
        # For now, select strategy based on weights and execute on single strategy
        # In a more advanced implementation, this could distribute load across
        # multiple strategies
        selected_strategy_type = self._select_strategy_by_weight(strategies)
        selected_strategy = strategies[selected_strategy_type]

        result = await self._execute_single_strategy(
            selected_strategy_type, selected_strategy, operation
        )
        return [result]

    def _select_strategy_by_weight(self, strategies: Dict[str, ProviderStrategy]) -> str:
        """Select a strategy based on configured weights."""
        # Filter weights for available strategies
        available_weights = {k: v for k, v in self._strategy_weights.items() if k in strategies}

        if not available_weights:
            return next(iter(strategies.keys()))

        # Weighted random selection
        total_weight = sum(available_weights.values())
        if total_weight == 0:
            return next(iter(strategies.keys()))

        # Use secrets for cryptographically secure randomness
        rand_val = secrets.SystemRandom().random() * total_weight
        cumulative = 0.0

        for strategy_type, weight in available_weights.items():
            cumulative += weight
            if rand_val <= cumulative:
                return strategy_type

        return next(iter(strategies.keys()))

    def _execute_single_strategy_sync(
        self, strategy_type: str, strategy: ProviderStrategy, operation: ProviderOperation
    ) -> StrategyExecutionResult:
        """Sync wrapper for parallel execution."""
        import asyncio

        return asyncio.run(self._execute_single_strategy(strategy_type, strategy, operation))

    async def _execute_single_strategy(
        self,
        strategy_type: str,
        strategy: ProviderStrategy,
        operation: ProviderOperation,
    ) -> StrategyExecutionResult:
        """Execute operation on a single strategy."""
        start_time = time.time()

        try:
            result = await strategy.execute_operation(operation)
            execution_time_ms = (time.time() - start_time) * 1000

            return StrategyExecutionResult(
                strategy_type=strategy_type,
                result=result,
                execution_time_ms=execution_time_ms,
                success=result.success,
            )

        except Exception as e:
            execution_time_ms = (time.time() - start_time) * 1000
            return StrategyExecutionResult(
                strategy_type=strategy_type,
                result=ProviderResult.error_result(
                    f"Strategy execution failed: {str(e)}", "STRATEGY_ERROR"
                ),
                execution_time_ms=execution_time_ms,
                success=False,
                error=e,
            )

    def _aggregate_results(
        self,
        execution_results: List[StrategyExecutionResult],
        operation: ProviderOperation,
    ) -> ProviderResult:
        """Aggregate results from multiple strategy executions."""
        successful_results = [r for r in execution_results if r.success]
        failed_results = [r for r in execution_results if not r.success]

        # Check failure threshold
        failure_rate = len(failed_results) / len(execution_results) if execution_results else 1.0
        if failure_rate > self._config.failure_threshold:
            return ProviderResult.error_result(
                f"Too many strategies failed: {len(failed_results)}/{len(execution_results)} (threshold: {self._config.failure_threshold})",
                "FAILURE_THRESHOLD_EXCEEDED",
                {
                    "failed_strategies": [r.strategy_type for r in failed_results],
                    "failure_rate": failure_rate,
                },
            )

        # Check minimum success requirement
        if len(successful_results) < self._config.min_success_count:
            return ProviderResult.error_result(
                f"Insufficient successful strategies: {len(successful_results)}/{self._config.min_success_count} required",
                "INSUFFICIENT_SUCCESS",
                {
                    "successful_strategies": [r.strategy_type for r in successful_results],
                    "required_count": self._config.min_success_count,
                },
            )

        # Apply aggregation policy
        if self._config.aggregation_policy == AggregationPolicy.FIRST_SUCCESS:
            return self._aggregate_first_success(successful_results)
        elif self._config.aggregation_policy == AggregationPolicy.MERGE_ALL:
            return self._aggregate_merge_all(successful_results)
        elif self._config.aggregation_policy == AggregationPolicy.BEST_PERFORMANCE:
            return self._aggregate_best_performance(successful_results)
        else:
            return self._aggregate_merge_all(successful_results)

    def _aggregate_first_success(self, results: List[StrategyExecutionResult]) -> ProviderResult:
        """Return the first successful result."""
        if not results:
            return ProviderResult.error_result("No successful results to aggregate", "NO_RESULTS")

        first_result = results[0]
        first_result.result.metadata["aggregation_policy"] = "first_success"
        first_result.result.metadata["selected_strategy"] = first_result.strategy_type
        return first_result.result

    def _aggregate_merge_all(self, results: List[StrategyExecutionResult]) -> ProviderResult:
        """Merge all successful results."""
        if not results:
            return ProviderResult.error_result("No successful results to aggregate", "NO_RESULTS")

        # Combine all data
        merged_data = {}
        all_metadata = {
            "aggregation_policy": "merge_all",
            "contributing_strategies": [],
        }

        for result in results:
            all_metadata["contributing_strategies"].append(result.strategy_type)

            if isinstance(result.result.data, dict):
                merged_data.update(result.result.data)
            elif isinstance(result.result.data, list):
                if "merged_list" not in merged_data:
                    merged_data["merged_list"] = []
                merged_data["merged_list"].extend(result.result.data)
            else:
                merged_data[result.strategy_type] = result.result.data

        return ProviderResult.success_result(merged_data, all_metadata)

    def _aggregate_best_performance(self, results: List[StrategyExecutionResult]) -> ProviderResult:
        """Return result from best performing strategy."""
        if not results:
            return ProviderResult.error_result("No successful results to aggregate", "NO_RESULTS")

        # Sort by execution time (fastest first)
        best_result = min(results, key=lambda r: r.execution_time_ms)
        best_result.result.metadata["aggregation_policy"] = "best_performance"
        best_result.result.metadata["selected_strategy"] = best_result.strategy_type
        best_result.result.metadata["execution_time_ms"] = best_result.execution_time_ms

        return best_result.result

    def get_capabilities(self) -> ProviderCapabilities:
        """
        Get combined capabilities from all composed strategies.

        Returns:
            Merged capabilities from all strategies
        """
        all_operations = set()
        combined_features = {}
        combined_limitations = {}
        performance_metrics = {}

        for strategy_type, strategy in self._strategies.items():
            try:
                capabilities = strategy.get_capabilities()
                all_operations.update(capabilities.supported_operations)
                combined_features.update(capabilities.features)
                combined_limitations.update(capabilities.limitations)
                performance_metrics[strategy_type] = capabilities.performance_metrics
            except Exception as e:
                self._self._logger.warning(
                    "Error getting capabilities from %s: %s", strategy_type, e
                )

        return ProviderCapabilities(
            provider_type=self.provider_type,
            supported_operations=list(all_operations),
            features=combined_features,
            limitations=combined_limitations,
            performance_metrics=performance_metrics,
        )

    def check_health(self) -> ProviderHealthStatus:
        """
        Check health of all composed strategies.

        Returns:
            Aggregated health status
        """
        start_time = time.time()
        healthy_count = 0
        total_count = len(self._strategies)
        health_details = {}

        for strategy_type, strategy in self._strategies.items():
            try:
                health = strategy.check_health()
                health_details[strategy_type] = {
                    "healthy": health.is_healthy,
                    "message": health.status_message,
                    "response_time_ms": health.response_time_ms,
                }
                if health.is_healthy:
                    healthy_count += 1
            except Exception as e:
                health_details[strategy_type] = {
                    "healthy": False,
                    "message": f"Health check failed: {str(e)}",
                    "error": str(e),
                }

        response_time_ms = (time.time() - start_time) * 1000
        health_ratio = healthy_count / total_count if total_count > 0 else 0.0

        # Consider composite healthy if majority of strategies are healthy
        is_healthy = health_ratio >= 0.5

        if is_healthy:
            return ProviderHealthStatus.healthy(
                f"Composite strategy healthy: {healthy_count}/{total_count} strategies operational",
                response_time_ms,
            )
        else:
            return ProviderHealthStatus.unhealthy(
                f"Composite strategy unhealthy: only {healthy_count}/{total_count} strategies operational",
                health_details,
            )

    def cleanup(self) -> None:
        """Clean up all composed strategies and resources."""
        try:
            # Shutdown executor
            self._executor.shutdown(wait=True)

            # Clean up all strategies
            for strategy_type, strategy in self._strategies.items():
                try:
                    strategy.cleanup()
                    self._self._logger.debug("Cleaned up strategy: %s", strategy_type)
                except Exception as e:
                    self._self._logger.warning(
                        "Error cleaning up strategy %s: %s", strategy_type, e
                    )

            self._strategies.clear()
            self._strategy_weights.clear()
            self._initialized = False

        except Exception as e:
            self._self._logger.warning("Error during composite strategy cleanup: %s", e)

    def __str__(self) -> str:
        """Return string representation for debugging."""
        strategy_types = list(self._strategies.keys())
        return f"CompositeProviderStrategy(strategies={strategy_types}, mode={self._config.mode.value})"

    def __repr__(self) -> str:
        """Detailed representation for debugging."""
        return (
            f"CompositeProviderStrategy("
            f"strategies={list(self._strategies.keys())}, "
            f"mode={self._config.mode.value}, "
            f"initialized={self._initialized}"
            f")"
        )
