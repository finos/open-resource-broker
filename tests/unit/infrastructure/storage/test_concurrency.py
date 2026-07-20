"""Unit tests for optimistic concurrency control utilities."""

from unittest.mock import patch

import pytest

from orb.domain.base.exceptions import ConcurrencyError
from orb.infrastructure.storage.concurrency import (
    OptimisticConcurrencyControl,
    get_optimistic_concurrency_control,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Entity:
    def __init__(self, eid: str, version: int = 0) -> None:
        self.id = eid
        self.version = version


class _EntityNoVersion:
    def __init__(self, eid: str) -> None:
        self.id = eid


# ---------------------------------------------------------------------------
# OptimisticConcurrencyControl.check_version
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckVersion:
    """check_version raises ConcurrencyError on version mismatch, passes otherwise."""

    def setup_method(self) -> None:
        self.occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl()

    def test_no_version_in_map_does_not_raise(self) -> None:
        """Entity not yet tracked — no conflict possible."""
        entity = _Entity("e1", version=0)
        self.occ.check_version(entity, "e1", {}, "Entity")

    def test_matching_version_does_not_raise(self) -> None:
        """Stored version matches entity version — no conflict."""
        entity = _Entity("e1", version=3)
        self.occ.check_version(entity, "e1", {"e1": 3}, "Entity")

    def test_mismatched_version_raises_concurrency_error(self) -> None:
        """Stored version differs from entity version — raises ConcurrencyError."""
        entity = _Entity("e1", version=0)
        with pytest.raises(ConcurrencyError) as exc_info:
            self.occ.check_version(entity, "e1", {"e1": 99}, "Entity")
        assert "e1" in str(exc_info.value)
        assert "99" in str(exc_info.value)

    def test_error_message_includes_expected_and_actual(self) -> None:
        """ConcurrencyError message contains both expected and actual versions."""
        entity = _Entity("order-42", version=5)
        with pytest.raises(ConcurrencyError) as exc_info:
            self.occ.check_version(entity, "order-42", {"order-42": 7}, "Order")
        msg = str(exc_info.value)
        assert "7" in msg  # expected
        assert "5" in msg  # got

    def test_entity_without_version_attribute_compared_as_none(self) -> None:
        """Entity lacking a version attribute uses None for comparison."""
        entity = _EntityNoVersion("e2")
        # version_map says 1, entity has no version (→ None) — should raise
        with pytest.raises(ConcurrencyError):
            self.occ.check_version(entity, "e2", {"e2": 1}, "Entity")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# OptimisticConcurrencyControl.increment_version
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIncrementVersion:
    """increment_version bumps the version in the map by one."""

    def setup_method(self) -> None:
        self.occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl()

    def test_new_entity_initialises_to_version_plus_one(self) -> None:
        version_map: dict[str, int] = {}
        entity = _Entity("e1", version=0)
        self.occ.increment_version(entity, "e1", version_map)
        assert version_map["e1"] == 1

    def test_existing_entry_is_overwritten(self) -> None:
        version_map = {"e1": 3}
        entity = _Entity("e1", version=5)
        self.occ.increment_version(entity, "e1", version_map)
        assert version_map["e1"] == 6

    def test_entity_without_version_uses_zero(self) -> None:
        version_map: dict[str, int] = {}
        entity = _EntityNoVersion("e3")
        self.occ.increment_version(entity, "e3", version_map)  # type: ignore[arg-type]
        assert version_map["e3"] == 1


# ---------------------------------------------------------------------------
# OptimisticConcurrencyControl.batch_check_versions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatchCheckVersions:
    """batch_check_versions iterates over all entities and delegates."""

    def setup_method(self) -> None:
        self.occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl()

    def test_all_matching_passes(self) -> None:
        entities = [_Entity("a", 1), _Entity("b", 2)]
        version_map = {"a": 1, "b": 2}
        self.occ.batch_check_versions(entities, lambda e: e.id, version_map, "Entity")

    def test_first_mismatch_raises_immediately(self) -> None:
        entities = [_Entity("a", 1), _Entity("b", 99)]
        version_map = {"a": 1, "b": 5}
        with pytest.raises(ConcurrencyError):
            self.occ.batch_check_versions(entities, lambda e: e.id, version_map, "Entity")

    def test_empty_list_does_not_raise(self) -> None:
        self.occ.batch_check_versions([], lambda e: e.id, {}, "Entity")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# OptimisticConcurrencyControl.batch_increment_versions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatchIncrementVersions:
    """batch_increment_versions updates every entity in the map."""

    def setup_method(self) -> None:
        self.occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl()

    def test_all_entities_incremented(self) -> None:
        entities = [_Entity("x", 0), _Entity("y", 4)]
        version_map: dict[str, int] = {}
        self.occ.batch_increment_versions(entities, lambda e: e.id, version_map)
        assert version_map["x"] == 1
        assert version_map["y"] == 5

    def test_empty_list_leaves_map_unchanged(self) -> None:
        version_map = {"z": 7}
        self.occ.batch_increment_versions([], lambda e: e.id, version_map)  # type: ignore[arg-type]
        assert version_map == {"z": 7}


# ---------------------------------------------------------------------------
# OptimisticConcurrencyControl.retry_on_concurrency_error decorator
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetryOnConcurrencyError:
    """retry_on_concurrency_error retries and re-raises after max_retries."""

    def test_function_succeeds_on_first_try(self) -> None:
        occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl(max_retries=3)
        calls = []

        @occ.retry_on_concurrency_error
        def fn() -> str:
            calls.append(1)
            return "ok"

        result = fn()
        assert result == "ok"
        assert len(calls) == 1

    def test_retries_up_to_max_then_raises(self) -> None:
        occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl(
            max_retries=2, retry_delay=0.0
        )
        calls = []

        @occ.retry_on_concurrency_error
        def always_fails() -> None:
            calls.append(1)
            raise ConcurrencyError("conflict")

        with pytest.raises(ConcurrencyError):
            always_fails()

        # 1 initial + 2 retries = 3 total
        assert len(calls) == 3

    def test_succeeds_after_one_transient_conflict(self) -> None:
        occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl(
            max_retries=3, retry_delay=0.0
        )
        attempt = [0]

        @occ.retry_on_concurrency_error
        def eventually_ok() -> str:
            attempt[0] += 1
            if attempt[0] < 2:
                raise ConcurrencyError("transient")
            return "done"

        result = eventually_ok()
        assert result == "done"
        assert attempt[0] == 2

    def test_non_concurrency_errors_propagate_immediately(self) -> None:
        occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl(max_retries=3)
        calls = []

        @occ.retry_on_concurrency_error
        def raises_value_error() -> None:
            calls.append(1)
            raise ValueError("not a concurrency issue")

        with pytest.raises(ValueError):
            raises_value_error()

        assert len(calls) == 1

    def test_decorator_preserves_function_name(self) -> None:
        occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl()

        @occ.retry_on_concurrency_error
        def my_func() -> None:
            pass  # type: ignore[return]

        assert my_func.__name__ == "my_func"

    def test_sleep_is_called_between_retries(self) -> None:
        occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl(
            max_retries=2, retry_delay=0.05
        )
        attempt = [0]

        @occ.retry_on_concurrency_error
        def fail_twice() -> None:
            attempt[0] += 1
            raise ConcurrencyError("conflict")

        with patch("time.sleep") as mock_sleep, pytest.raises(ConcurrencyError):
            fail_twice()

        # sleep called on each retry (2 retries → 2 sleeps)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(0.05)


# ---------------------------------------------------------------------------
# OptimisticConcurrencyControl constructor defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConstructorDefaults:
    """Default values are applied correctly."""

    def test_default_max_retries_is_three(self) -> None:
        occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl()
        assert occ.max_retries == 3

    def test_default_retry_delay_is_point_one(self) -> None:
        occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl()
        assert occ.retry_delay == 0.1

    def test_custom_values_accepted(self) -> None:
        occ: OptimisticConcurrencyControl[_Entity] = OptimisticConcurrencyControl(
            max_retries=7, retry_delay=1.5
        )
        assert occ.max_retries == 7
        assert occ.retry_delay == 1.5


# ---------------------------------------------------------------------------
# get_optimistic_concurrency_control singleton
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetOptimisticConcurrencyControl:
    """Module-level singleton accessor."""

    def test_returns_instance(self) -> None:
        result = get_optimistic_concurrency_control()
        assert isinstance(result, OptimisticConcurrencyControl)

    def test_returns_same_instance_on_repeated_calls(self) -> None:
        a = get_optimistic_concurrency_control()
        b = get_optimistic_concurrency_control()
        assert a is b

    def test_singleton_reset_creates_new_instance(self) -> None:
        import orb.infrastructure.storage.concurrency as concurrency_module

        original = concurrency_module._optimistic_concurrency_control
        try:
            concurrency_module._optimistic_concurrency_control = None
            fresh = get_optimistic_concurrency_control()
            assert isinstance(fresh, OptimisticConcurrencyControl)
        finally:
            concurrency_module._optimistic_concurrency_control = original
