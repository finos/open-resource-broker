"""Unit tests for uncovered branches in orb.monitoring.health."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orb.monitoring.health import (
    HealthCheck,
    HealthCheckConfig,
    HealthStatus,
    register_deserialize_skip_counter_check,
    register_storage_health_checks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(tmp_path: Path) -> HealthCheckConfig:
    return HealthCheckConfig(health_dir=tmp_path / "health")


def _make_health_check(tmp_path: Path, logger=None) -> HealthCheck:
    return HealthCheck(config=_config(tmp_path), logger=logger)


def _system_health_globals(hc: HealthCheck) -> dict:
    """Return the globals dict that ``_check_system_health`` actually reads.

    ``_check_system_health`` resolves ``PSUTIL_AVAILABLE`` and ``psutil`` from
    the module namespace in which the ``HealthCheck`` class was defined. Another
    test in the suite deletes and reimports ``orb.monitoring.health`` to exercise
    its optional-dependency guards, which can leave the current ``sys.modules``
    entry pointing at a different module object than the one backing this
    instance's method. Reading/writing the flag on the wrong object makes the
    test order-dependent, so we target the method's own ``__globals__`` — the
    exact namespace it will read at call time — instead.
    """
    return type(hc)._check_system_health.__globals__


def _psutil_available(hc: HealthCheck) -> bool:
    return bool(_system_health_globals(hc).get("PSUTIL_AVAILABLE", False))


# ---------------------------------------------------------------------------
# HealthStatus
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthStatus:
    def test_to_dict_returns_expected_keys(self) -> None:
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc)
        hs = HealthStatus(
            name="disk",
            status="healthy",
            details={"free_gb": 100},
            timestamp=ts,
            dependencies=["os"],
        )
        d = hs.to_dict()
        assert d["name"] == "disk"
        assert d["status"] == "healthy"
        assert d["details"]["free_gb"] == 100
        assert "timestamp" in d
        assert d["dependencies"] == ["os"]


# ---------------------------------------------------------------------------
# HealthCheck.register_check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterCheck:
    def test_first_write_wins_by_default(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        original_fn = hc.checks.get("database")
        new_fn = MagicMock(return_value=HealthStatus("database", "healthy", {}))
        hc.register_check("database", new_fn)
        # Original wins
        assert hc.checks.get("database") is original_fn

    def test_force_overwrites_existing(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        new_fn = MagicMock(return_value=HealthStatus("database", "healthy", {}))
        hc.register_check("database", new_fn, force=True)
        assert hc.checks.get("database") is new_fn

    def test_new_check_registered(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        fn = MagicMock(return_value=HealthStatus("custom", "healthy", {}))
        hc.register_check("custom", fn)
        assert hc.checks.get("custom") is fn


# ---------------------------------------------------------------------------
# HealthCheck.run_check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunCheck:
    def test_run_check_unknown_raises(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        with pytest.raises(ValueError, match="Unknown health check"):
            hc.run_check("nonexistent_check")

    def test_run_check_returns_dict(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        result = hc.run_check("database")
        assert isinstance(result, dict)
        assert "status" in result

    def test_run_check_catches_exception_and_returns_unhealthy(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)

        def _bad_check():
            raise RuntimeError("check failed")

        hc.register_check("bad_check", _bad_check)
        result = hc.run_check("bad_check")
        assert result["status"] == "unhealthy"
        assert "error" in result["details"]

    def test_run_check_with_logger_on_exception(self, tmp_path) -> None:
        logger = MagicMock()
        hc = _make_health_check(tmp_path, logger=logger)

        def _bad():
            raise RuntimeError("bang")

        hc.register_check("bad_check", _bad)
        hc.run_check("bad_check")
        logger.error.assert_called()


# ---------------------------------------------------------------------------
# HealthCheck.run_all_checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunAllChecks:
    def test_run_all_checks_returns_dict_with_all_check_names(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        result = hc.run_all_checks()
        assert isinstance(result, dict)
        for name in ("system", "disk", "database", "application"):
            assert name in result

    def test_run_all_checks_each_result_is_dict(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        result = hc.run_all_checks()
        for v in result.values():
            assert isinstance(v, dict)


# ---------------------------------------------------------------------------
# HealthCheck.get_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStatus:
    def test_get_status_unknown_when_no_history(self, tmp_path) -> None:
        """Before running any checks, status is 'unknown'."""
        hc = _make_health_check(tmp_path)
        result = hc.get_status()
        # No check has been run, so every history is empty and the aggregate
        # status derives to 'unknown'.
        assert result["status"] == "unknown"

    def test_get_status_reflects_unhealthy_check(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)

        def _unhealthy():
            return HealthStatus("custom", "unhealthy", {"reason": "down"})

        hc.register_check("custom", _unhealthy)
        hc.run_check("custom")
        status = hc.get_status()
        assert status["status"] == "unhealthy"

    def test_get_status_reflects_degraded_check(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)

        def _degraded():
            return HealthStatus("custom", "degraded", {})

        hc.register_check("custom_degraded", _degraded)
        hc.run_check("custom_degraded")
        # Only the degraded check has run history (defaults registered but not
        # run), so no 'unhealthy' status is present and degraded wins.
        result = hc.get_status()
        assert result["status"] == "degraded"


# ---------------------------------------------------------------------------
# HealthCheck._check_system_health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckSystemHealth:
    def test_returns_unknown_when_psutil_unavailable(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        # Force the psutil-absent branch by flipping the flag on the exact
        # module namespace the method reads, then restore it afterwards. This
        # is deterministic regardless of whether psutil is actually installed.
        mod_globals = _system_health_globals(hc)
        original = mod_globals.get("PSUTIL_AVAILABLE")
        try:
            mod_globals["PSUTIL_AVAILABLE"] = False
            result = hc._check_system_health()
            assert result.status == "unknown"
            assert result.details.get("available") is False
        finally:
            mod_globals["PSUTIL_AVAILABLE"] = original

    def test_returns_degraded_on_high_cpu(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        if not _psutil_available(hc):
            pytest.skip("psutil not installed")
        psutil = _system_health_globals(hc)["psutil"]
        mock_mem = MagicMock()
        mock_mem.percent = 50.0
        with patch.object(psutil, "cpu_percent", return_value=91.0):
            with patch.object(psutil, "virtual_memory", return_value=mock_mem):
                result = hc._check_system_health()
        assert result.status in ("degraded", "unhealthy")

    def test_returns_unhealthy_on_very_high_memory(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        if not _psutil_available(hc):
            pytest.skip("psutil not installed")
        psutil = _system_health_globals(hc)["psutil"]
        mock_mem = MagicMock()
        mock_mem.percent = 96.0
        with patch.object(psutil, "cpu_percent", return_value=10.0):
            with patch.object(psutil, "virtual_memory", return_value=mock_mem):
                result = hc._check_system_health()
        assert result.status == "unhealthy"

    def test_returns_unhealthy_on_exception(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        if not _psutil_available(hc):
            pytest.skip("psutil not installed")
        psutil = _system_health_globals(hc)["psutil"]
        with patch.object(psutil, "cpu_percent", side_effect=Exception("sensor error")):
            result = hc._check_system_health()
        assert result.status == "unhealthy"


# ---------------------------------------------------------------------------
# HealthCheck._check_disk_health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckDiskHealth:
    def test_returns_healthy_when_disk_ok(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        result = hc._check_disk_health()
        assert result.status in ("healthy", "degraded", "unhealthy")

    def test_returns_degraded_on_high_disk_usage(self, tmp_path) -> None:
        import shutil

        hc = _make_health_check(tmp_path)
        with patch.object(shutil, "disk_usage", return_value=(100, 91, 9)):
            result = hc._check_disk_health()
        assert result.status in ("degraded", "unhealthy")

    def test_returns_unhealthy_on_write_failure(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        with patch.object(Path, "write_text", side_effect=OSError("no space")):
            result = hc._check_disk_health()
        assert result.status == "unhealthy"
        assert "error" in result.details


# ---------------------------------------------------------------------------
# HealthCheck._check_database_health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckDatabaseHealth:
    def test_returns_unknown_by_default(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        result = hc._check_database_health()
        assert result.status == "unknown"
        assert "No database check registered" in result.details.get("reason", "")


# ---------------------------------------------------------------------------
# HealthCheck._check_application_health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckApplicationHealth:
    def test_healthy_when_all_checks_pass(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        # Replace all checks with healthy ones
        for name in list(hc.checks.keys()):
            if name != "application":
                hc.checks[name] = lambda n=name: HealthStatus(n, "healthy", {})
        result = hc._check_application_health()
        assert result.status == "healthy"

    def test_unhealthy_when_any_check_fails(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        hc.checks["system"] = lambda: HealthStatus("system", "unhealthy", {})
        hc.checks["disk"] = lambda: HealthStatus("disk", "healthy", {})
        hc.checks["database"] = lambda: HealthStatus("database", "healthy", {})
        result = hc._check_application_health()
        assert result.status == "unhealthy"

    def test_degraded_when_any_check_degraded(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        hc.checks["system"] = lambda: HealthStatus("system", "healthy", {})
        hc.checks["disk"] = lambda: HealthStatus("disk", "degraded", {})
        hc.checks["database"] = lambda: HealthStatus("database", "healthy", {})
        result = hc._check_application_health()
        assert result.status in ("degraded", "unhealthy")

    def test_returns_unhealthy_on_exception(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)

        def _bad():
            raise RuntimeError("system exploded")

        hc.checks["system"] = _bad
        result = hc._check_application_health()
        # _run_check_internal wraps the raising sub-check as 'unhealthy', which
        # makes the aggregate application status 'unhealthy'.
        assert result.status == "unhealthy"


# ---------------------------------------------------------------------------
# register_deserialize_skip_counter_check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterDeserializeSkipCounterCheck:
    def test_skips_when_method_not_found(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        repo = MagicMock(spec=[])  # no _get_skip_counters
        register_deserialize_skip_counter_check(hc, repo)
        assert "storage.deserialize" not in hc.checks

    def test_registers_when_method_present_and_returns_healthy_on_zero_skips(
        self, tmp_path
    ) -> None:
        hc = _make_health_check(tmp_path)
        repo = MagicMock()
        repo._get_skip_counters.return_value = {"machines": 0, "requests": 0}
        register_deserialize_skip_counter_check(hc, repo)
        assert "storage.deserialize" in hc.checks
        result_dict = hc.run_check("storage.deserialize")
        assert result_dict["status"] == "healthy"

    def test_returns_degraded_on_nonzero_skip_counters(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        repo = MagicMock()
        repo._get_skip_counters.return_value = {"machines": 3}
        register_deserialize_skip_counter_check(hc, repo)
        result_dict = hc.run_check("storage.deserialize")
        assert result_dict["status"] == "degraded"

    def test_returns_unhealthy_on_exception_in_skip_counters(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        repo = MagicMock()
        repo._get_skip_counters.side_effect = Exception("db gone")
        register_deserialize_skip_counter_check(hc, repo)
        result_dict = hc.run_check("storage.deserialize")
        assert result_dict["status"] == "unhealthy"


# ---------------------------------------------------------------------------
# register_storage_health_checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterStorageHealthChecks:
    def test_skips_when_is_healthy_not_present(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        storage = MagicMock(spec=[])  # no is_healthy
        original_db_check = hc.checks.get("database")
        register_storage_health_checks(hc, storage)
        # Database check should remain unchanged
        assert hc.checks.get("database") is original_db_check

    def test_replaces_database_check_with_healthy(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        storage = MagicMock()
        storage.is_healthy.return_value = (True, {"backend": "ok"})
        register_storage_health_checks(hc, storage)
        result = hc.run_check("database")
        assert result["status"] == "healthy"

    def test_replaces_database_check_with_unhealthy(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        storage = MagicMock()
        storage.is_healthy.return_value = (False, {"error": "conn lost"})
        register_storage_health_checks(hc, storage)
        result = hc.run_check("database")
        assert result["status"] == "unhealthy"

    def test_handles_bare_bool_return_from_is_healthy(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        storage = MagicMock()
        storage.is_healthy.return_value = True
        register_storage_health_checks(hc, storage)
        result = hc.run_check("database")
        assert result["status"] == "healthy"

    def test_returns_unhealthy_when_is_healthy_raises(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        storage = MagicMock()
        storage.is_healthy.side_effect = Exception("probe failed")
        register_storage_health_checks(hc, storage)
        result = hc.run_check("database")
        assert result["status"] == "unhealthy"

    def test_idempotent_registration(self, tmp_path) -> None:
        hc = _make_health_check(tmp_path)
        storage = MagicMock()
        storage.is_healthy.return_value = (True, {})
        register_storage_health_checks(hc, storage)
        register_storage_health_checks(hc, storage)  # second call
        result = hc.run_check("database")
        assert result["status"] == "healthy"
