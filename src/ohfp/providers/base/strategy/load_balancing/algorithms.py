"""Load balancing algorithms enumeration."""

from enum import Enum

from domain.base.dependency_injection import injectable


@injectable
class LoadBalancingAlgorithm(str, Enum):
    """Load balancing algorithms."""

    ROUND_ROBIN = "round_robin"
    WEIGHTED_ROUND_ROBIN = "weighted_round_robin"
    LEAST_CONNECTIONS = "least_connections"
    LEAST_RESPONSE_TIME = "least_response_time"
    RANDOM = "random"
    WEIGHTED_RANDOM = "weighted_random"
    HASH_BASED = "hash_based"
    ADAPTIVE = "adaptive"


@injectable
class HealthCheckMode(str, Enum):
    """Health check modes for load balancing."""

    DISABLED = "disabled"
    PASSIVE = "passive"  # Monitor during regular operations
    ACTIVE = "active"  # Periodic health checks
    HYBRID = "hybrid"  # Both passive and active
