"""Unit tests for BaseStorageStrategy (base/strategy.py)."""

from typing import Any, Optional
from unittest.mock import patch

import pytest

from orb.infrastructure.storage.base.strategy import BaseStorageStrategy
from orb.infrastructure.storage.exceptions import StorageError

# ---------------------------------------------------------------------------
# Concrete implementation for testing
# ---------------------------------------------------------------------------


class _ConcreteStrategy(BaseStorageStrategy):
    """Minimal concrete strategy backed by an in-memory dict."""

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[str, dict[str, Any]] = {}

    def save(self, entity_id: str, data: dict[str, Any]) -> None:  # type: ignore[override]
        self._store[entity_id] = data

    def find_by_id(self, entity_id: str) -> Optional[dict[str, Any]]:  # type: ignore[override]
        return self._store.get(entity_id)

    def find_all(self) -> dict[str, dict[str, Any]]:  # type: ignore[override]
        return dict(self._store)

    def delete(self, entity_id: str) -> None:
        self._store.pop(entity_id, None)

    def exists(self, entity_id: str) -> bool:
        return entity_id in self._store


# ---------------------------------------------------------------------------
# is_healthy — base default
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsHealthyDefault:
    def test_default_returns_false_with_reason(self) -> None:
        strategy = _ConcreteStrategy()
        healthy, details = strategy.is_healthy()
        assert healthy is False
        assert "reason" in details


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanup:
    def test_marks_strategy_as_closed(self) -> None:
        strategy = _ConcreteStrategy()
        strategy.cleanup()
        assert strategy._is_closed is True

    def test_cleanup_calls_rollback_if_in_transaction(self) -> None:
        strategy = _ConcreteStrategy()
        # Manually start a transaction
        strategy._store["e1"] = {"id": "e1"}
        strategy.begin_transaction()
        assert strategy._in_transaction is True
        strategy.cleanup()
        assert strategy._is_closed is True
        assert strategy._in_transaction is False

    def test_cleanup_raises_storage_error_on_exception(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._in_transaction = True

        # Make rollback_transaction raise so cleanup wraps it
        with patch.object(strategy, "rollback_transaction", side_effect=RuntimeError("boom")):
            with pytest.raises(StorageError):
                strategy.cleanup()


# ---------------------------------------------------------------------------
# context manager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContextManager:
    def test_enter_returns_self(self) -> None:
        strategy = _ConcreteStrategy()
        result = strategy.__enter__()
        strategy.cleanup()
        assert result is strategy

    def test_enter_raises_when_closed(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._is_closed = True
        with pytest.raises(StorageError):
            strategy.__enter__()

    def test_exit_calls_cleanup(self) -> None:
        strategy = _ConcreteStrategy()
        with patch.object(strategy, "cleanup") as mock_cleanup:
            strategy.__exit__(None, None, None)
            mock_cleanup.assert_called_once()

    def test_exit_rolls_back_on_exception(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._store["snap"] = {"id": "snap"}
        strategy.begin_transaction()

        class _Boom(Exception):
            pass

        # Patch cleanup to prevent it from calling rollback again after our mock
        with patch.object(strategy, "rollback_transaction") as mock_rb:
            with patch.object(strategy, "cleanup"):
                strategy.__exit__(_Boom, _Boom("oops"), None)
            mock_rb.assert_called_once()

    def test_exit_does_not_suppress_exceptions(self) -> None:
        strategy = _ConcreteStrategy()
        result = strategy.__exit__(ValueError, ValueError("err"), None)
        assert result is False

    def test_context_manager_via_with_block(self) -> None:
        strategy = _ConcreteStrategy()
        with strategy:
            strategy.save("k", {"id": "k"})
        assert strategy._is_closed is True


# ---------------------------------------------------------------------------
# begin_transaction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBeginTransaction:
    def test_sets_in_transaction(self) -> None:
        strategy = _ConcreteStrategy()
        strategy.begin_transaction()
        assert strategy._in_transaction is True

    def test_raises_when_already_in_transaction(self) -> None:
        strategy = _ConcreteStrategy()
        strategy.begin_transaction()
        with pytest.raises(StorageError):
            strategy.begin_transaction()

    def test_takes_snapshot_of_current_state(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._store["e1"] = {"id": "e1"}
        strategy.begin_transaction()
        assert strategy._transaction_snapshot is not None
        assert "e1" in strategy._transaction_snapshot

    def test_snapshot_converts_list_to_dict(self) -> None:
        class _ListStrategy(_ConcreteStrategy):
            def find_all(self) -> list:  # type: ignore[override]
                return [{"id": "lx"}]

        strategy = _ListStrategy()
        strategy.begin_transaction()
        assert isinstance(strategy._transaction_snapshot, dict)
        assert "lx" in strategy._transaction_snapshot


# ---------------------------------------------------------------------------
# commit_transaction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCommitTransaction:
    def test_clears_in_transaction(self) -> None:
        strategy = _ConcreteStrategy()
        strategy.begin_transaction()
        strategy.commit_transaction()
        assert strategy._in_transaction is False
        assert strategy._transaction_snapshot is None

    def test_raises_when_not_in_transaction(self) -> None:
        strategy = _ConcreteStrategy()
        with pytest.raises(StorageError):
            strategy.commit_transaction()


# ---------------------------------------------------------------------------
# rollback_transaction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRollbackTransaction:
    def test_clears_in_transaction(self) -> None:
        strategy = _ConcreteStrategy()
        strategy.begin_transaction()
        strategy.rollback_transaction()
        assert strategy._in_transaction is False
        assert strategy._transaction_snapshot is None

    def test_raises_when_not_in_transaction(self) -> None:
        strategy = _ConcreteStrategy()
        with pytest.raises(StorageError):
            strategy.rollback_transaction()


# ---------------------------------------------------------------------------
# save_batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSaveBatch:
    def test_saves_all_entities(self) -> None:
        strategy = _ConcreteStrategy()
        strategy.save_batch({"a": {"id": "a"}, "b": {"id": "b"}, "c": {"id": "c"}})
        assert strategy.exists("a")
        assert strategy.exists("b")
        assert strategy.exists("c")

    def test_wraps_exception_as_storage_error(self) -> None:
        strategy = _ConcreteStrategy()
        with patch.object(strategy, "save", side_effect=RuntimeError("disk full")):
            with pytest.raises(StorageError):
                strategy.save_batch({"x": {"id": "x"}})


# ---------------------------------------------------------------------------
# delete_batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteBatch:
    def test_deletes_all_entity_ids(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._store = {"a": {}, "b": {}, "c": {}}
        strategy.delete_batch(["a", "b"])
        assert not strategy.exists("a")
        assert not strategy.exists("b")
        assert strategy.exists("c")

    def test_wraps_exception_as_storage_error(self) -> None:
        strategy = _ConcreteStrategy()
        with patch.object(strategy, "delete", side_effect=RuntimeError("gone")):
            with pytest.raises(StorageError):
                strategy.delete_batch(["x"])


# ---------------------------------------------------------------------------
# _get_entity_id_from_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetEntityIdFromDictStrategy:
    def test_id_key(self) -> None:
        strategy = _ConcreteStrategy()
        assert strategy._get_entity_id_from_dict({"id": "abc"}) == "abc"

    def test_request_id_key(self) -> None:
        strategy = _ConcreteStrategy()
        assert strategy._get_entity_id_from_dict({"request_id": "r1"}) == "r1"

    def test_machine_id_key(self) -> None:
        strategy = _ConcreteStrategy()
        assert strategy._get_entity_id_from_dict({"machine_id": "m1"}) == "m1"

    def test_template_id_key(self) -> None:
        strategy = _ConcreteStrategy()
        assert strategy._get_entity_id_from_dict({"template_id": "t1"}) == "t1"

    def test_unknown_raises(self) -> None:
        strategy = _ConcreteStrategy()
        with pytest.raises(ValueError, match="Cannot determine ID"):
            strategy._get_entity_id_from_dict({"foo": "bar"})


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCount:
    def test_count_empty(self) -> None:
        strategy = _ConcreteStrategy()
        assert strategy.count() == 0

    def test_count_after_saves(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._store = {"a": {}, "b": {}}
        assert strategy.count() == 2

    def test_count_with_list_find_all(self) -> None:
        class _ListStrategy(_ConcreteStrategy):
            def find_all(self) -> list:  # type: ignore[override]
                return [{"id": "x"}, {"id": "y"}]

        strategy = _ListStrategy()
        assert strategy.count() == 2


# ---------------------------------------------------------------------------
# find_by_criteria / _matches_criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindByCriteria:
    def test_simple_field_match(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._store = {
            "a": {"id": "a", "status": "active"},
            "b": {"id": "b", "status": "inactive"},
        }
        result = strategy.find_by_criteria({"status": "active"})
        assert len(result) == 1
        assert result[0]["id"] == "a"

    def test_no_match_returns_empty(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._store = {"a": {"id": "a", "status": "active"}}
        assert strategy.find_by_criteria({"status": "gone"}) == []

    def test_nested_dot_notation(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._store = {
            "a": {"id": "a", "meta": {"env": "prod"}},
        }
        result = strategy.find_by_criteria({"meta.env": "prod"})
        assert len(result) == 1

    def test_nested_dot_notation_missing_intermediate_key(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._store = {"a": {"id": "a", "meta": {}}}
        result = strategy.find_by_criteria({"meta.env": "prod"})
        assert result == []

    def test_list_field_value_in_list(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._store = {"a": {"id": "a", "tags": ["foo", "bar"]}}
        result = strategy.find_by_criteria({"tags": "foo"})
        assert len(result) == 1

    def test_list_field_value_not_in_list(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._store = {"a": {"id": "a", "tags": ["foo"]}}
        result = strategy.find_by_criteria({"tags": "missing"})
        assert result == []

    def test_missing_field_does_not_match(self) -> None:
        strategy = _ConcreteStrategy()
        strategy._store = {"a": {"id": "a"}}  # no "color" key
        result = strategy.find_by_criteria({"color": "red"})
        assert result == []

    def test_find_by_criteria_with_list_storage(self) -> None:
        class _ListStrategy(_ConcreteStrategy):
            def find_all(self) -> list:  # type: ignore[override]
                return [{"id": "x", "status": "ok"}, {"id": "y", "status": "nope"}]

        strategy = _ListStrategy()
        result = strategy.find_by_criteria({"status": "ok"})
        assert len(result) == 1
        assert result[0]["id"] == "x"
