"""Load balancing configuration."""

from dataclasses import dataclass

from .algorithms import HealthCheckMode, LoadBalancingAlgorithm


@dataclass
class LoadBalancingConfig:
    """Configuration for load balancing strategy."""

    algorithm: LoadBalancingAlgorithm = LoadBalancingAlgorithm.ROUND_ROBIN
    health_check_mode: HealthCheckMode = HealthCheckMode.HYBRID
    health_check_interval_seconds: float = 30.0
    unhealthy_threshold: int = 3  # Consecutive failures before marking unhealthy
    recovery_threshold: int = 2  # Consecutive successes before marking healthy
    max_connections_per_strategy: int = 100
    response_time_window_size: int = 10  # Number of recent requests to track
    weight_adjustment_factor: float = 0.1  # For adaptive algorithms
    sticky_sessions: bool = False
    session_timeout_seconds: float = 300.0

    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.health_check_interval_seconds <= 0:
            raise ValueError("health_check_interval_seconds must be positive") from e
        if self.unhealthy_threshold < 1:
            raise ValueError("unhealthy_threshold must be at least 1") from e
        if self.recovery_threshold < 1:
            raise ValueError("recovery_threshold must be at least 1") from e
        if self.max_connections_per_strategy < 1:
            raise ValueError("max_connections_per_strategy must be at least 1") from e
        if not 0 < self.weight_adjustment_factor <= 1:
            raise ValueError("weight_adjustment_factor must be between 0 and 1") from e
