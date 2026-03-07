"""Provider Selector Utilities - Shared logic for strategy selection.

This module extracts common functionality used by multiple selector implementations,
following DRY principle and avoiding anti-patterns.
"""

from typing import TYPE_CHECKING, Any, Optional

from orb.providers.base.strategy.provider_strategy import ProviderStrategy

if TYPE_CHECKING:
    from orb.providers.base.strategy.provider_selector import SelectionCriteria


def is_strategy_suitable(
    strategy: ProviderStrategy,
    metrics: Optional[dict[str, Any]],
    criteria: "SelectionCriteria",
) -> bool:
    """Check if strategy meets the selection criteria.

    This is a shared utility function used by all selector implementations
    to determine if a strategy is suitable for selection.

    Args:
        strategy: Provider strategy to evaluate
        metrics: Optional performance metrics for the strategy
        criteria: Selection criteria to check against

    Returns:
        True if strategy meets all criteria, False otherwise
    """
    # Check exclusions
    if criteria.exclude_strategies and strategy.provider_type in criteria.exclude_strategies:
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
        success_rate = metrics.get("success_rate", 0.0)
        if success_rate < criteria.min_success_rate:
            return False

        avg_response_time = metrics.get("average_response_time_ms", 0.0)
        if avg_response_time > criteria.max_response_time_ms:
            return False

    # Check custom filter
    if criteria.custom_filter:
        metrics_dict = metrics if metrics is not None else {}
        if not criteria.custom_filter(strategy, metrics_dict):
            return False

    return True
