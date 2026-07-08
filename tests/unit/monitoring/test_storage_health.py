"""Tests for the storage-aware ``database`` health check wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orb.monitoring.health import (
    HealthCheck,
    HealthCheckConfig,
    register_storage_health_checks,
)


def _build_health_check(tmp_path: Path) -> HealthCheck:
    return HealthCheck(config=HealthCheckConfig(health_dir=tmp_path))


# ── register_storage_health_checks ─────────────────────────────────────────


def test_register_replaces_default_database_check_with_healthy_storage(
    tmp_path: Path,
) -> None:
    hc = _build_health_check(tmp_path)
    assert hc.run_check("database")["status"] == "unknown"  # placeholder

    class FakeStorage:
        def is_healthy(self) -> tuple[bool, dict[str, Any]]:
            return True, {"type": "json", "entity_count": 3}

    register_storage_health_checks(hc, FakeStorage())
    result = hc.run_check("database")
    assert result["status"] == "healthy"
    assert result["details"] == {"type": "json", "entity_count": 3}


def test_register_marks_unhealthy_when_storage_reports_unhealthy(
    tmp_path: Path,
) -> None:
    hc = _build_health_check(tmp_path)

    class BrokenStorage:
        def is_healthy(self) -> tuple[bool, dict[str, Any]]:
            return False, {"type": "sql", "reason": "connection refused"}

    register_storage_health_checks(hc, BrokenStorage())
    result = hc.run_check("database")
    assert result["status"] == "unhealthy"
    assert result["details"]["reason"] == "connection refused"


def test_register_handles_exception_from_probe(tmp_path: Path) -> None:
    hc = _build_health_check(tmp_path)

    class CrashStorage:
        def is_healthy(self) -> tuple[bool, dict[str, Any]]:
            raise RuntimeError("boom")

    register_storage_health_checks(hc, CrashStorage())
    result = hc.run_check("database")
    assert result["status"] == "unhealthy"
    assert "boom" in result["details"]["error"]


def test_register_tolerates_bare_bool_return(tmp_path: Path) -> None:
    """Legacy strategies might return a bare bool — wrap it cleanly."""
    hc = _build_health_check(tmp_path)

    class LegacyStorage:
        def is_healthy(self) -> bool:
            return True

    register_storage_health_checks(hc, LegacyStorage())
    result = hc.run_check("database")
    assert result["status"] == "healthy"


def test_register_no_op_when_storage_lacks_is_healthy(tmp_path: Path) -> None:
    """Storage objects without is_healthy keep the placeholder check."""
    hc = _build_health_check(tmp_path)

    class NoHealthApi:
        pass

    register_storage_health_checks(hc, NoHealthApi())
    result = hc.run_check("database")
    assert result["status"] == "unknown"  # placeholder still in place


def test_register_uses_force_to_override_existing(tmp_path: Path) -> None:
    """The constructor pre-registers a placeholder; we must overwrite it."""
    hc = _build_health_check(tmp_path)
    # Sanity: default placeholder is in place.
    assert hc.checks["database"].__name__ == "_check_database_health"

    class FakeStorage:
        def is_healthy(self) -> tuple[bool, dict[str, Any]]:
            return True, {}

    register_storage_health_checks(hc, FakeStorage())
    assert hc.checks["database"].__name__ == "_check_storage_backend_health"


# ── force flag on register_check ──────────────────────────────────────────


def test_register_check_is_first_write_wins_without_force(tmp_path: Path) -> None:
    hc = _build_health_check(tmp_path)

    def first() -> Any:
        return None

    def second() -> Any:
        return None

    hc.register_check("custom", first)
    hc.register_check("custom", second)  # ignored
    assert hc.checks["custom"] is first


def test_register_check_force_overwrites(tmp_path: Path) -> None:
    hc = _build_health_check(tmp_path)

    def first() -> Any:
        return None

    def second() -> Any:
        return None

    hc.register_check("custom", first)
    hc.register_check("custom", second, force=True)
    assert hc.checks["custom"] is second


# ── JSON strategy is_healthy ───────────────────────────────────────────────


def test_json_strategy_is_healthy_for_fresh_install(tmp_path: Path) -> None:
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy

    strategy = JSONStorageStrategy(
        file_path=str(tmp_path / "data.json"),
        create_dirs=True,
        entity_type="machines",
    )
    healthy, details = strategy.is_healthy()
    assert healthy is True
    assert details["state"] == "empty"
    assert details["entity_type"] == "machines"


def test_json_strategy_is_healthy_with_records(tmp_path: Path) -> None:
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy

    p = tmp_path / "data.json"
    p.write_text('{"id-1": {"name": "x", "status": "running"}}', encoding="utf-8")
    strategy = JSONStorageStrategy(
        file_path=str(p),
        create_dirs=True,
        entity_type="machines",
    )
    healthy, details = strategy.is_healthy()
    assert healthy is True
    assert details["entity_count"] == 1
    assert details["sample_keys"] == ["name", "status"]


def test_json_strategy_unhealthy_on_malformed_file(tmp_path: Path) -> None:
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy

    p = tmp_path / "data.json"
    p.write_text("not json at all", encoding="utf-8")
    strategy = JSONStorageStrategy(
        file_path=str(p),
        create_dirs=True,
        entity_type="machines",
    )
    healthy, details = strategy.is_healthy()
    assert healthy is False
    assert "error" in details


def test_json_strategy_unhealthy_on_non_object_root(tmp_path: Path) -> None:
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy

    p = tmp_path / "data.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    strategy = JSONStorageStrategy(
        file_path=str(p),
        create_dirs=True,
        entity_type="machines",
    )
    healthy, details = strategy.is_healthy()
    assert healthy is False
    assert "expected JSON object" in details["reason"]


def test_json_strategy_unhealthy_when_record_is_not_dict(tmp_path: Path) -> None:
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy

    p = tmp_path / "data.json"
    p.write_text('{"id-1": "should-be-a-dict"}', encoding="utf-8")
    strategy = JSONStorageStrategy(
        file_path=str(p),
        create_dirs=True,
        entity_type="machines",
    )
    healthy, details = strategy.is_healthy()
    assert healthy is False
    assert "sampled record" in details["reason"]
