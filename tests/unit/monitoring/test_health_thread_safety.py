"""Thread safety and idempotency tests for HealthCheck."""

import threading
import time
from pathlib import Path
from unittest.mock import patch

from orb.monitoring.health import HealthCheck, HealthCheckConfig, HealthStatus


def _make_check(name: str, status: str = "healthy") -> HealthStatus:
    return HealthStatus(name=name, status=status, details={})


def _make_health_check() -> HealthCheck:
    """Create a HealthCheck with background checker disabled and no filesystem side effects."""
    config = HealthCheckConfig(
        health_dir=Path("/tmp/test-health"),
    )
    with patch("orb.monitoring.health.Path.mkdir"):
        return HealthCheck(config=config)


class TestRunAllChecksThreadSafety:
    def test_run_all_checks_no_error_when_register_called_concurrently(self) -> None:
        """No RuntimeError when register_check() is called while run_all_checks() iterates."""
        hc = _make_health_check()

        # Pre-populate with enough checks that iteration takes a little time
        for i in range(20):
            hc.checks[f"pre_{i}"] = lambda i=i: _make_check(f"pre_{i}")
            hc.status_history[f"pre_{i}"] = []

        errors: list[Exception] = []

        def keep_registering() -> None:
            for i in range(50):
                try:
                    hc.register_check(f"concurrent_{i}", lambda i=i: _make_check(f"concurrent_{i}"))
                except Exception as e:
                    errors.append(e)
                time.sleep(0.001)

        registrar = threading.Thread(target=keep_registering)
        registrar.start()

        runtime_errors: list[RuntimeError] = []
        for _ in range(10):
            try:
                hc.run_all_checks()
            except RuntimeError as e:
                runtime_errors.append(e)

        registrar.join(timeout=5)

        assert not runtime_errors, f"RuntimeError raised during iteration: {runtime_errors}"
        assert not errors, f"Unexpected errors in registrar thread: {errors}"


class TestRegisterCheckIdempotent:
    def test_register_check_idempotent(self) -> None:
        """Registering the same name twice keeps the first function."""
        hc = _make_health_check()

        first_func = lambda: _make_check("probe", "healthy")
        second_func = lambda: _make_check("probe", "unhealthy")

        hc.register_check("probe", first_func)
        hc.register_check("probe", second_func)

        assert hc.checks["probe"] is first_func, (
            "Second registration should not overwrite the first"
        )

    def test_register_check_new_name_works(self) -> None:
        """Registering a new check name adds it to self.checks."""
        hc = _make_health_check()

        func = lambda: _make_check("new_check")
        hc.register_check("new_check", func)

        assert "new_check" in hc.checks
        assert hc.checks["new_check"] is func
        assert "new_check" in hc.status_history
