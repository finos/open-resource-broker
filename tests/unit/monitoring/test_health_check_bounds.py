"""Provider and storage health checks are time-limited and never pile up threads."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from orb.monitoring.health import HealthCheck, HealthCheckConfig, HealthStatus


def _health(tmp_path: Path, timeout: float = 0.2) -> HealthCheck:
    health = HealthCheck(config=HealthCheckConfig(health_dir=tmp_path / "health"))
    health.check_timeout_seconds = timeout
    # Only the checks a test registers (and the application summary): the host's
    # own disk and memory state must not colour the result.
    for builtin in ("system", "disk", "database"):
        health.checks.pop(builtin, None)
        health.check_kinds.pop(builtin, None)
        health.status_history.pop(builtin, None)
    return health


def _count_threads(prefix: str) -> int:
    return sum(1 for t in threading.enumerate() if t.name.startswith(prefix) and t.is_alive())


@pytest.mark.unit
def test_hung_provider_check_times_out_as_degraded(tmp_path: Path) -> None:
    release = threading.Event()
    health = _health(tmp_path)
    health.register_check(
        "slow", lambda: release.wait(30) or HealthStatus("slow", "healthy", {}), kind="provider"
    )
    try:
        started = time.monotonic()
        result = health.run_check("slow")
        assert time.monotonic() - started < 2
    finally:
        release.set()
    assert result["status"] == "degraded"
    assert "timed out" in result["details"]["error"]


@pytest.mark.unit
def test_hung_core_check_times_out_as_unhealthy(tmp_path: Path) -> None:
    release = threading.Event()
    health = _health(tmp_path)
    health.register_check(
        "db", lambda: release.wait(30) or HealthStatus("db", "healthy", {}), kind="core"
    )
    try:
        result = health.run_check("db")
    finally:
        release.set()
    assert result["status"] == "unhealthy"


@pytest.mark.unit
def test_no_second_run_starts_while_the_first_is_still_going(tmp_path: Path) -> None:
    release = threading.Event()
    calls = []

    def hang() -> HealthStatus:
        calls.append(1)
        release.wait(30)
        return HealthStatus("slow", "healthy", {})

    health = _health(tmp_path)
    health.register_check("slow", hang, kind="provider")
    try:
        for _ in range(10):
            health.run_check("slow")
        assert len(calls) == 1
        assert _count_threads("orb-health-slow") == 1
    finally:
        release.set()


@pytest.mark.unit
def test_run_all_checks_costs_one_time_limit_not_one_per_check(tmp_path: Path) -> None:
    release = threading.Event()
    health = _health(tmp_path, timeout=0.3)
    for name in ("aws", "ec2", "k8s"):
        health.register_check(
            name, lambda: release.wait(30) or HealthStatus("x", "healthy", {}), kind="provider"
        )
    try:
        started = time.monotonic()
        results = health.run_all_checks()
        elapsed = time.monotonic() - started
    finally:
        release.set()
    assert elapsed < 1.5
    assert all(results[n]["status"] == "degraded" for n in ("aws", "ec2", "k8s"))


@pytest.mark.unit
def test_fast_checks_and_a_recovered_provider_report_normally(tmp_path: Path) -> None:
    health = _health(tmp_path)
    health.register_check(
        "fast", lambda: HealthStatus("fast", "healthy", {"ok": True}), kind="provider"
    )
    assert health.run_check("fast")["status"] == "healthy"
    assert health.run_check("fast")["status"] == "healthy"


@pytest.mark.unit
def test_exception_in_a_bounded_check_is_reported_generically(tmp_path: Path) -> None:
    def boom() -> HealthStatus:
        raise RuntimeError("secret detail")

    health = _health(tmp_path)
    health.register_check("boom", boom, kind="provider")
    result = health.run_check("boom")
    assert result["status"] == "unhealthy"
    assert "secret detail" not in repr(result)


def _slow_ok(name: str, seconds: float):
    def check() -> HealthStatus:
        time.sleep(seconds)
        return HealthStatus(name, "healthy", {})

    return check


@pytest.mark.unit
def test_slow_but_healthy_check_is_reported_healthy(tmp_path: Path) -> None:
    health = _health(tmp_path, timeout=2.0)
    health.register_check("database", _slow_ok("database", 0.3), kind="core")

    assert health.run_check("database")["status"] == "healthy"


@pytest.mark.unit
def test_application_is_healthy_when_a_check_is_slow_but_healthy(tmp_path: Path) -> None:
    health = _health(tmp_path, timeout=2.0)
    health.register_check("database", _slow_ok("database", 0.3), kind="core")
    health.register_check("aws", _slow_ok("aws", 0.3), kind="provider")

    results = health.run_all_checks()

    assert results["database"]["status"] == "healthy"
    assert results["aws"]["status"] == "healthy"
    assert results["application"]["status"] == "healthy"
    assert health.get_status()["status"] == "healthy"


@pytest.mark.unit
def test_application_check_run_on_its_own_is_healthy_too(tmp_path: Path) -> None:
    health = _health(tmp_path, timeout=2.0)
    health.register_check("database", _slow_ok("database", 0.3), kind="core")

    assert health.run_check("application")["status"] == "healthy"


@pytest.mark.unit
def test_overlapping_callers_both_get_the_real_result(tmp_path: Path) -> None:
    health = _health(tmp_path, timeout=2.0)
    calls = []

    def slow() -> HealthStatus:
        calls.append(1)
        time.sleep(0.4)
        return HealthStatus("database", "healthy", {})

    health.register_check("database", slow, kind="core")
    results: list[str] = []

    def call() -> None:
        results.append(health.run_check("database")["status"])

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
        time.sleep(0.1)  # the second caller arrives while the first run is in flight
    for t in threads:
        t.join()

    assert results == ["healthy", "healthy"]
    assert len(calls) == 1


@pytest.mark.unit
def test_overlapping_run_all_checks_calls_are_both_healthy(tmp_path: Path) -> None:
    health = _health(tmp_path, timeout=2.0)
    health.register_check("database", _slow_ok("database", 0.4), kind="core")
    outcomes: list[str] = []

    def call() -> None:
        outcomes.append(health.run_all_checks()["application"]["status"])

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
        time.sleep(0.1)
    for t in threads:
        t.join()

    assert outcomes == ["healthy", "healthy"]


@pytest.mark.unit
def test_forever_hang_stays_bounded_for_every_caller_without_piling_up_threads(
    tmp_path: Path,
) -> None:
    release = threading.Event()
    health = _health(tmp_path, timeout=0.3)
    health.register_check(
        "aws", lambda: release.wait(30) or HealthStatus("aws", "healthy", {}), kind="provider"
    )
    try:
        started = time.monotonic()
        for _ in range(6):
            assert health.run_all_checks()["application"]["status"] == "degraded"
        # The run's limit counts from when it started, so later callers return at once.
        assert time.monotonic() - started < 2
        assert _count_threads("orb-health-aws") == 1
    finally:
        release.set()
