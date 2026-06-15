"""Public AWS provider value objects.

Re-exports AWS-specific value objects from their canonical locations so that
callers outside the internal sub-packages can use a stable, shallow import path:

    from orb.providers.aws.value_objects import AWSAllocationStrategy
"""

from orb.providers.aws.domain.template.value_objects import (
    CANONICAL_ALLOCATION_STRATEGIES,
    AWSAllocationStrategy,
    normalise_allocation_strategy,
)

__all__: list[str] = [
    "AWSAllocationStrategy",
    "CANONICAL_ALLOCATION_STRATEGIES",
    "normalise_allocation_strategy",
]
