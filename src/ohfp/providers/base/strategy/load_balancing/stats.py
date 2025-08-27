"""Load balancing statistics tracking."""

from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StrategyStats:
    """Statistics for a single strategy in load balancing."""

    active_connections: int = 0
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    is_healthy: bool = True
    last_health_check: Optional[float] = None
    response_times: deque = field(default_factory=lambda: deque(maxlen=10))  # Recent response times
    average_response_time: float = 0.0
    weight: float = 1.0

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_requests == 0:
            return 100.0
        return (self.successful_requests / self.total_requests) * 100.0

    @property
    def failure_rate(self) -> float:
        """Calculate failure rate percentage."""
        return 100.0 - self.success_rate

    def record_request_start(self) -> None:
        """Record the start of a request."""
        self.active_connections += 1
        self.total_requests += 1

    def record_request_end(self, success: bool, response_time_ms: float) -> None:
        """Record the end of a request."""
        self.active_connections = max(0, self.active_connections - 1)

        if success:
            self.successful_requests += 1
            self.consecutive_successes += 1
            self.consecutive_failures = 0
        else:
            self.failed_requests += 1
            self.consecutive_failures += 1
            self.consecutive_successes = 0

        # Update response time statistics
        self.response_times.append(response_time_ms)
        if self.response_times:
            self.average_response_time = sum(self.response_times) / len(self.response_times)

    def reset_stats(self) -> None:
        """Reset all statistics."""
        self.active_connections = 0
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self.is_healthy = True
        self.last_health_check = None
        self.response_times.clear()
        self.average_response_time = 0.0
        self.weight = 1.0
