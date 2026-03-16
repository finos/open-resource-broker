"""Port interface for health check monitoring."""

from abc import ABC, abstractmethod
from typing import Any


class HealthCheckPort(ABC):
    """Abstract port for health check monitoring."""

    @abstractmethod
    def register_check(self, name: str, check_fn: Any) -> None:
        """Register a named health check function."""
        pass

    @abstractmethod
    def run_check(self, name: str) -> dict[str, Any]:
        """Run a specific health check by name and return its result."""
        pass

    @abstractmethod
    def run_all_checks(self) -> dict[str, Any]:
        """Run all registered health checks and return results."""
        pass

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        """Get the current health status summary."""
        pass
