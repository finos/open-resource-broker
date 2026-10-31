"""Health check monitoring for the application."""

import concurrent.futures
import logging
import threading
import time
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

# Standard logging: the monitoring package must not import infrastructure
# (that would close an infrastructure -> providers -> monitoring cycle).
_logger = logging.getLogger(__name__)

CHECK_FAILED_TEXT = "Check failed; see server logs for details"
CHECK_TIMED_OUT_TEXT = "Check timed out; see server logs for details"

# Checks of these kinds call out to providers or storage and can hang with the
# remote API, so each run is time-limited.
_BOUNDED_KINDS = frozenset({"core", "provider"})
DEFAULT_CHECK_TIMEOUT_SECONDS = 5.0


def failure_details(check_name: str, error: object) -> dict[str, Any]:
    """Details for a failed check: generic text out, the real error to the log.

    Health details are served by unauthenticated endpoints, so exception text
    (which can embed account identifiers, paths or hostnames) never goes in them.
    """
    _logger.warning("Health check %s failed: %s", check_name, error)
    return {"error": CHECK_FAILED_TEXT}


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
        # Classification of each check for readiness gating. Keyed by check
        # name; values are "core", "provider", or "system".
        self.check_kinds: dict[str, str] = {}
        self.status_history: dict[str, list[HealthStatus]] = {}
        self._lock = threading.Lock()
        self.check_timeout_seconds = DEFAULT_CHECK_TIMEOUT_SECONDS
        # The check call still running in its thread for each bounded check.
        self._running_checks: dict[str, tuple[concurrent.futures.Future[HealthStatus], float]] = {}

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

    def register_check(
        self, name: str, check_fn: Any, *, force: bool = False, kind: str = "system"
    ) -> None:
        """Register a named health check function.

        First-write-wins by default. Pass ``force=True`` to overwrite an
        existing registration — used by the bootstrap to replace the
        placeholder ``database`` check with a storage-backed one once the
        active StoragePort is resolved.

        ``kind`` classifies the check for readiness gating (``"core"``,
        ``"provider"``, or ``"system"``). Only ``"core"`` checks gate
        ``get_readiness``.
        """
        with self._lock:
            if name in self.checks and not force:
                return
            self.checks[name] = check_fn
            self.check_kinds[name] = kind
            self.status_history.setdefault(name, [])

    def run_check(self, name: str) -> dict[str, Any]:
        """Run a specific health check by name and return its result as a dict."""
        status = self._run_check_internal(name)
        return status.to_dict()

    def run_all_checks(self) -> dict[str, Any]:
        """Run all registered health checks and return results as dicts."""
        with self._lock:
            names = list(self.checks)
        if not names:
            return {}
        results = self._run_others([n for n in names if n != "application"])
        if "application" in names:
            # The application summary reads the results just gathered instead of
            # running every other check a second time.
            self._record("application", self._application_status(results))
            results["application"] = self.status_history["application"][-1]
        return {name: results[name].to_dict() for name in names}

    def _run_others(self, names: list[str]) -> dict[str, HealthStatus]:
        """Run the named checks side by side.

        A slow provider then costs one time limit in total, not one per check.
        """
        if not names:
            return {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(names), thread_name_prefix="orb-health"
        ) as pool:
            return dict(zip(names, pool.map(self._run_check_internal, names)))

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

    def get_readiness(self) -> dict[str, Any]:
        """Get readiness derived from core-dependency checks only.

        Only checks registered with ``kind="core"`` (storage/database) gate
        readiness. Provider-connectivity checks (aws, ec2, kubernetes_api)
        are excluded, so an unreachable optional provider does not make the
        service report as not-ready. With no core-check history the status
        derives to ``"unknown"``.
        """
        with self._lock:
            core_names = {name for name, kind in self.check_kinds.items() if kind == "core"}
            checks_status = {
                name: (history[-1].to_dict() if history else None)
                for name, history in self.status_history.items()
                if name in core_names
            }

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

    def _record(self, name: str, status: HealthStatus) -> None:
        with self._lock:
            self.status_history[name].append(status)
            # Keep only last 100 statuses
            if len(self.status_history[name]) > 100:
                self.status_history[name].pop(0)

    def _call_check(self, name: str) -> HealthStatus:
        """Call a check; provider and storage checks are time-limited.

        A check that is still running when its limit passes cannot be
        interrupted, so no second run is started while it is: thread use stays
        at one per check however often a hung provider is polled. A caller that
        arrives while a run is in flight waits for that run (up to the limit,
        counted from when the run started) and gets its real result; the
        timed-out status is only returned when a run actually exceeded the limit.
        """
        check_fn = self.checks[name]
        kind = self.check_kinds.get(name, "system")
        if kind not in _BOUNDED_KINDS:
            return check_fn()

        with self._lock:
            entry = self._running_checks.get(name)
            if entry is None or entry[0].done():
                future: concurrent.futures.Future[HealthStatus] = concurrent.futures.Future()

                def _run(fut: concurrent.futures.Future[HealthStatus] = future) -> None:
                    if not fut.set_running_or_notify_cancel():
                        return
                    try:
                        fut.set_result(check_fn())
                    except BaseException as exc:  # delivered to the waiting callers
                        fut.set_exception(exc)

                entry = (future, time.monotonic())
                self._running_checks[name] = entry
                threading.Thread(target=_run, name=f"orb-health-{name}", daemon=True).start()

        running, started_at = entry
        remaining = self.check_timeout_seconds - (time.monotonic() - started_at)
        done, _ = concurrent.futures.wait([running], timeout=max(0.0, remaining))
        if not done:
            _logger.warning(
                "Health check %s did not finish within %.1fs", name, self.check_timeout_seconds
            )
            return self._timed_out_status(name, kind)
        return running.result()

    @staticmethod
    def _timed_out_status(name: str, kind: str) -> HealthStatus:
        return HealthStatus(
            name=name,
            status="degraded" if kind == "provider" else "unhealthy",
            details={"error": CHECK_TIMED_OUT_TEXT},
            dependencies=[],
        )

    def _run_check_internal(self, name: str) -> HealthStatus:
        """Run a specific health check, returning a HealthStatus object.

        Called by run_check and run_all_checks. Catches all exceptions so a
        failing check never kills the caller.
        """
        if name not in self.checks:
            raise ValueError(f"Unknown health check: {name}")

        try:
            status = self._call_check(name)
            self._record(name, status)
            return status
        except Exception as e:
            if self._logger:
                self._logger.error("Health check %s failed: %s", name, e, exc_info=True)
            return HealthStatus(
                name=name,
                status="unhealthy",
                details=failure_details(name, e),
                dependencies=[],
            )

    def _register_default_checks(self) -> None:
        """Register default health checks. Called from __init__."""
        self.register_check("system", self._check_system_health, kind="system")
        self.register_check("disk", self._check_disk_health, kind="system")
        self.register_check("database", self._check_database_health, kind="core")
        self.register_check("application", self._check_application_health, kind="system")

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
                details=failure_details("system", e),
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
                details=failure_details("disk", e),
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
        """Check overall application health (runs the other checks)."""
        try:
            with self._lock:
                names = [n for n in self.checks if n != "application"]
            return self._application_status(self._run_others(names))
        except Exception as e:
            return HealthStatus(
                name="application",
                status="unhealthy",
                details=failure_details("application", e),
                dependencies=["system", "aws", "database"],
            )

    @staticmethod
    def _application_status(results: dict[str, HealthStatus]) -> HealthStatus:
        """Summarise the other checks' results into the application status."""
        status_counts: dict[str, int] = {"healthy": 0, "degraded": 0, "unhealthy": 0, "unknown": 0}
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
                details=failure_details("storage.deserialize", exc),
                dependencies=["database"],
            )
        total_skipped = sum(counters.values())
        return HealthStatus(
            name="storage.deserialize",
            status="degraded" if total_skipped > 0 else "healthy",
            details={"skipped_rows": counters, "total_skipped": total_skipped},
            dependencies=["database"],
        )

    # Deliberately kind="system" (the default), NOT "core": a non-zero skip
    # counter means some rows returned incomplete (a data-quality signal), and
    # the only "unhealthy" path is a transient failure to READ the counter — in
    # neither case can the storage backend not serve reads (that capability is
    # covered by the separate "core" ``database`` check). So this must not gate
    # readiness / take the pod out of rotation.
    health_check.register_check(
        "storage.deserialize", _check_deserialize_skip_counters, force=True, kind="system"
    )


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
                details=failure_details("database", exc),
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
        # Strategy details are served verbatim by /health: keep raw error text
        # (and anything else free-form) out of them.
        if "error" in details:
            _logger.warning("Storage health probe reported: %s", details["error"])
            details["error"] = CHECK_FAILED_TEXT
        return HealthStatus(
            name="database",
            status="healthy" if healthy else "unhealthy",
            details=details,
            dependencies=["database"],
        )

    # Replace the placeholder ``database`` check installed by the
    # HealthCheck constructor. force=True is required because the
    # default is first-write-wins. Storage is a core dependency, so it
    # gates readiness.
    health_check.register_check("database", _check_storage_backend_health, force=True, kind="core")
