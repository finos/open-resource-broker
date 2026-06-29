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
    # Seed psutil.cpu_percent so subsequent ``interval=None`` calls return
    # an average since the previous call rather than the spike-prone
    # zeroth value. Without this seed, the first health probe always
    # reports CPU=0 and the next probe can report a wildly inflated %
    # which trips the >95 unhealthy threshold for one cycle.
    psutil.cpu_percent(interval=None)
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

    def register_check(self, name: str, check_fn: Any, *, force: bool = False) -> None:
        """Register a named health check function.

        First-write-wins by default. Pass ``force=True`` to overwrite an
        existing registration — used by the bootstrap to replace the
        placeholder ``database`` check with a storage-backed one once the
        active StoragePort is resolved.
        """
        with self._lock:
            if name in self.checks and not force:
                return
            self.checks[name] = check_fn
            self.status_history.setdefault(name, [])

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
        """Check CPU and memory pressure.

        Disk is intentionally NOT included here — ``_check_disk_health``
        is the canonical disk signal and includes a write-probe. Mixing
        disk into ``system`` causes the same disk-full host to trip two
        separate checks, doubling the noise.

        Uses a short blocking ``cpu_percent`` sample so each probe
        returns a real value rather than the zeroth/garbage value
        ``cpu_percent()`` returns when called without an interval.
        """
        if not PSUTIL_AVAILABLE:
            return HealthStatus(
                name="system",
                status="unknown",
                details={"available": False, "reason": "psutil not installed"},
                dependencies=["os"],
            )

        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)  # type: ignore[union-attr]
            memory = psutil.virtual_memory()  # type: ignore[union-attr]

            status = "healthy"
            if cpu_percent > 90 or memory.percent > 90:
                status = "degraded"
            if cpu_percent > 95 or memory.percent > 95:
                status = "unhealthy"

            return HealthStatus(
                name="system",
                status=status,
                details={
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory.percent,
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


def register_deserialize_skip_counter_check(
    health_check: HealthCheckPort,
    repository: Any,
) -> None:
    """Register a ``storage.deserialize`` health check backed by a repository mixin.

    The check is ``healthy`` when no rows have been skipped.  Any non-zero
    skip counter flips the check to ``degraded`` so operators can see that
    list operations are returning incomplete results without surfacing a hard
    failure.

    Args:
        health_check: The application HealthCheck instance.
        repository: A ``StorageRepositoryMixin`` subclass exposing
            ``_get_skip_counters()``.  If the method is absent the check is
            not registered.
    """
    get_counters = getattr(repository, "_get_skip_counters", None)
    if not callable(get_counters):
        return

    def _check_deserialize_skip_counters() -> HealthStatus:
        try:
            raw = get_counters()  # type: ignore[call-arg]  # callable validated above
            counters: dict[str, int] = raw if isinstance(raw, dict) else {}
        except Exception as exc:
            return HealthStatus(
                name="storage.deserialize",
                status="unhealthy",
                details={"error": str(exc)},
                dependencies=["database"],
            )
        total_skipped = sum(counters.values())
        return HealthStatus(
            name="storage.deserialize",
            status="degraded" if total_skipped > 0 else "healthy",
            details={"skipped_rows": counters, "total_skipped": total_skipped},
            dependencies=["database"],
        )

    health_check.register_check("storage.deserialize", _check_deserialize_skip_counters, force=True)


def register_storage_health_checks(
    health_check: HealthCheckPort,
    storage_port: Any,
) -> None:
    """Replace the default ``database`` health check with a storage-aware one.

    The default ``HealthCheck._check_database_health`` returns ``unknown``
    because the core monitoring module doesn't know which backend is
    configured. ``StoragePort`` implementations expose ``is_healthy()``
    (returning ``(bool, details)``), and this registers a check that
    delegates to the active strategy regardless of provider.

    Idempotent: safe to call multiple times (the registry overwrites by
    name).

    Args:
        health_check: The application HealthCheck instance.
        storage_port: A concrete StoragePort with an ``is_healthy`` method.
            If the object doesn't implement ``is_healthy`` the default
            ``unknown`` placeholder is left in place.
    """
    probe = getattr(storage_port, "is_healthy", None)
    if not callable(probe):
        return

    def _check_storage_backend_health() -> HealthStatus:
        try:
            result = probe()
        except Exception as exc:
            return HealthStatus(
                name="database",
                status="unhealthy",
                details={"error": str(exc)},
                dependencies=["database"],
            )
        # Normalise the return shape — strategies return (bool, dict),
        # but tolerate the legacy bare-bool form too.
        if isinstance(result, tuple) and len(result) == 2:
            healthy_raw, details_raw = result
            healthy = bool(healthy_raw)
            details: dict[str, Any] = dict(details_raw) if isinstance(details_raw, dict) else {}
        else:
            healthy = bool(result)
            details = {}
        return HealthStatus(
            name="database",
            status="healthy" if healthy else "unhealthy",
            details=details,
            dependencies=["database"],
        )

    # Replace the placeholder ``database`` check installed by the
    # HealthCheck constructor. force=True is required because the
    # default is first-write-wins.
    health_check.register_check("database", _check_storage_backend_health, force=True)
