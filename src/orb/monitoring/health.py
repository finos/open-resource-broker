"""Health check monitoring for the application."""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Optional monitoring dependencies
try:
    import psutil  # type: ignore[import-not-found]

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None

from orb.domain.base.ports.health_check_port import HealthCheckPort
from orb.domain.base.ports.logging_port import LoggingPort


@dataclass
class HealthCheckConfig:
    """Typed configuration for HealthCheck."""

    health_dir: Path


@dataclass
class HealthStatus:
    """Health check status."""

    name: str
    status: str  # 'healthy', 'degraded', 'unhealthy'
    details: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert health status to dictionary."""
        return {
            "name": self.name,
            "status": self.status,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
            "dependencies": self.dependencies,
        }


class HealthCheck(HealthCheckPort):
    """Health check implementation."""

    def __init__(
        self,
        config: HealthCheckConfig,
        logger: LoggingPort | None = None,
    ) -> None:
        """Initialize health check."""
        self._logger = logger
        self.config = config
        self.checks: dict[str, Callable[[], HealthStatus]] = {}
        self.status_history: dict[str, list[HealthStatus]] = {}
        self._lock = threading.Lock()

        # Create health check directory with PermissionError fallbacks
        self.health_dir = config.health_dir
        try:
            self.health_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            if self._logger:
                self._logger.warning(
                    "Permission denied creating health dir %s, falling back to ~/.orb/work/health",
                    self.health_dir,
                )
            self.health_dir = Path.home() / ".orb" / "work" / "health"
            try:
                self.health_dir.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                import tempfile

                if self._logger:
                    self._logger.warning(
                        "Permission denied creating health dir %s, falling back to tempdir",
                        self.health_dir,
                    )
                self.health_dir = Path(tempfile.mkdtemp(prefix="orb-health-"))

        # Register default health checks
        self._register_default_checks()

    def register_check(self, name: str, check_fn: Any) -> None:
        """Register a named health check function (idempotent — first registration wins)."""
        with self._lock:
            if name in self.checks:
                return
            self.checks[name] = check_fn
            self.status_history[name] = []

    def run_check(self, name: str) -> dict[str, Any]:
        """Run a specific health check by name and return its result as a dict."""
        status = self._run_check_internal(name)
        return status.to_dict()

    def run_all_checks(self) -> dict[str, Any]:
        """Run all registered health checks and return results as dicts."""
        with self._lock:
            names = list(self.checks)
        return {name: self._run_check_internal(name).to_dict() for name in names}

    def get_status(self) -> dict[str, Any]:
        """Get the current health status summary."""
        with self._lock:
            checks_status = {
                name: (history[-1].to_dict() if history else None)
                for name, history in self.status_history.items()
            }

        # Derive overall status from latest per-check statuses
        statuses = [v["status"] for v in checks_status.values() if v is not None]
        if "unhealthy" in statuses:
            overall = "unhealthy"
        elif "degraded" in statuses:
            overall = "degraded"
        elif statuses:
            overall = "healthy"
        else:
            overall = "unknown"

        return {"status": overall, "checks": checks_status}

    def _run_check_internal(self, name: str) -> HealthStatus:
        """Run a specific health check, returning a HealthStatus object.

        Called by run_check and run_all_checks. Catches all exceptions so a
        failing check never kills the caller.
        """
        if name not in self.checks:
            raise ValueError(f"Unknown health check: {name}")

        try:
            status = self.checks[name]()
            with self._lock:
                self.status_history[name].append(status)
                # Keep only last 100 statuses
                if len(self.status_history[name]) > 100:
                    self.status_history[name].pop(0)
            return status
        except Exception as e:
            if self._logger:
                self._logger.error("Health check %s failed: %s", name, e, exc_info=True)
            return HealthStatus(
                name=name,
                status="unhealthy",
                details={"error": str(e)},
                dependencies=[],
            )

    def _register_default_checks(self) -> None:
        """Register default health checks. Called from __init__."""
        self.register_check("system", self._check_system_health)
        self.register_check("disk", self._check_disk_health)
        self.register_check("database", self._check_database_health)
        self.register_check("application", self._check_application_health)

    def _check_system_health(self) -> HealthStatus:
        """Check system health."""
        if not PSUTIL_AVAILABLE:
            return HealthStatus(
                name="system",
                status="unknown",
                details={"available": False, "reason": "psutil not installed"},
                dependencies=["os"],
            )

        try:
            cpu_percent = psutil.cpu_percent()  # type: ignore[union-attr]
            memory = psutil.virtual_memory()  # type: ignore[union-attr]
            disk = psutil.disk_usage("/")  # type: ignore[union-attr]

            status = "healthy"
            if cpu_percent > 90 or memory.percent > 90 or disk.percent > 90:
                status = "degraded"
            if cpu_percent > 95 or memory.percent > 95 or disk.percent > 95:
                status = "unhealthy"

            return HealthStatus(
                name="system",
                status=status,
                details={
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory.percent,
                    "disk_percent": disk.percent,
                },
                dependencies=["os"],
            )
        except Exception as e:
            return HealthStatus(
                name="system",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["os"],
            )

    def _check_disk_health(self) -> HealthStatus:
        """Check disk health."""
        try:
            # Check write access
            test_file = self.health_dir / "test.txt"
            test_file.write_text("test")
            test_file.unlink()

            # Check disk space
            import shutil

            total, used, free = shutil.disk_usage("/")
            percent_used = (used / total) * 100

            status = "healthy"
            if percent_used > 90:
                status = "degraded"
            if percent_used > 95:
                status = "unhealthy"

            return HealthStatus(
                name="disk",
                status=status,
                details={
                    "total_gb": total // (2**30),
                    "used_gb": used // (2**30),
                    "free_gb": free // (2**30),
                    "percent_used": percent_used,
                },
                dependencies=["os"],
            )
        except Exception as e:
            return HealthStatus(
                name="disk",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["os"],
            )

    def _check_database_health(self) -> HealthStatus:
        """Check database health.

        Without raw dict config, database type is unknown at this level.
        AWS-specific checks are registered via register_aws_health_checks().
        """
        return HealthStatus(
            name="database",
            status="unknown",
            details={"reason": "No database check registered"},
            dependencies=["database"],
        )

    def _check_application_health(self) -> HealthStatus:
        """Check overall application health."""
        try:
            results = {
                name: self._run_check_internal(name)
                for name in self.checks
                if name != "application"
            }

            status_counts: dict[str, int] = {
                "healthy": 0,
                "degraded": 0,
                "unhealthy": 0,
                "unknown": 0,
            }
            for result in results.values():
                status_counts[result.status] = status_counts.get(result.status, 0) + 1

            if status_counts["unhealthy"] > 0:
                overall = "unhealthy"
            elif status_counts["degraded"] > 0:
                overall = "degraded"
            else:
                overall = "healthy"

            return HealthStatus(
                name="application",
                status=overall,
                details={
                    "status_counts": status_counts,
                    "checks": {name: result.status for name, result in results.items()},
                },
                dependencies=["system", "aws", "database"],
            )
        except Exception as e:
            return HealthStatus(
                name="application",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["system", "aws", "database"],
            )
