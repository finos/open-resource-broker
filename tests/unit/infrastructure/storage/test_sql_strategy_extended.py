"""Extended unit tests for SQLStorageStrategy using sqlite :memory:."""

from unittest.mock import MagicMock

import pytest


def _make_strategy(table_name="test_entities", columns=None):
    """Create an in-memory SQLStorageStrategy for tests."""
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

    if columns is None:
        columns = {"id": "TEXT PRIMARY KEY", "data": "TEXT"}
    return SQLStorageStrategy(
        config={"type": "sqlite", "name": ":memory:"},
        table_name=table_name,
        columns=columns,
    )


def _make_versioned_strategy():
    """Create strategy with a version column to test optimistic concurrency."""
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

    return SQLStorageStrategy(
        config={"type": "sqlite", "name": ":memory:"},
        table_name="versioned_ents",
        columns={"id": "TEXT PRIMARY KEY", "data": "TEXT", "version": "INTEGER"},
    )


# ---------------------------------------------------------------------------
# is_healthy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyIsHealthy:
    def test_healthy_returns_true_when_table_exists(self) -> None:
        strategy = _make_strategy()
        healthy, details = strategy.is_healthy()
        assert healthy is True
        assert details.get("table_exists") is True

    def test_unhealthy_when_connection_manager_reports_unhealthy(self) -> None:
        strategy = _make_strategy()
        strategy.connection_manager.get_connection_info = MagicMock(
            return_value={"database_type": "sqlite", "healthy": False}
        )
        healthy, details = strategy.is_healthy()
        assert healthy is False
        assert "reason" in details

    def test_unhealthy_when_table_check_raises(self) -> None:
        strategy = _make_strategy()
        strategy.connection_manager.get_connection_info = MagicMock(
            return_value={"database_type": "sqlite", "healthy": True}
        )
        strategy.connection_manager.table_exists = MagicMock(side_effect=RuntimeError("no conn"))
        healthy, details = strategy.is_healthy()
        assert healthy is False
        assert "error" in details

    def test_unhealthy_when_table_does_not_exist(self) -> None:
        strategy = _make_strategy()
        strategy.connection_manager.get_connection_info = MagicMock(
            return_value={"database_type": "sqlite", "healthy": True}
        )
        strategy.connection_manager.table_exists = MagicMock(return_value=False)
        healthy, details = strategy.is_healthy()
        assert healthy is False
        assert "reason" in details


# ---------------------------------------------------------------------------
# save — insert path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategySave:
    def test_save_and_find_by_id(self) -> None:
        strategy = _make_strategy()
        strategy.save("e1", {"id": "e1", "data": "hello"})
        result = strategy.find_by_id("e1")
        assert result is not None
        assert result["id"] == "e1"

    def test_save_update_existing(self) -> None:
        strategy = _make_strategy()
        strategy.save("u1", {"id": "u1", "data": "v1"})
        strategy.save("u1", {"id": "u1", "data": "v2"})
        result = strategy.find_by_id("u1")
        assert result is not None
        assert result["data"] == "v2"

    def test_save_raises_storage_error_on_failure(self) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_strategy()
        strategy.connection_manager.get_session = MagicMock(side_effect=RuntimeError("db down"))
        with pytest.raises(StorageError):
            strategy.save("fail", {"id": "fail"})


# ---------------------------------------------------------------------------
# save — optimistic concurrency (versioned table)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyConcurrency:
    def test_concurrency_error_on_version_mismatch(self) -> None:
        from orb.domain.base.exceptions import ConcurrencyError

        strategy = _make_versioned_strategy()
        # Insert with version 0
        strategy.save("cv1", {"id": "cv1", "data": "first", "version": 0})
        # Now simulate writing version 2 as if someone else already wrote version 1
        # The CAS expects DB to have version=(2-1)=1 but it has 0
        with pytest.raises(ConcurrencyError):
            strategy.save("cv1", {"id": "cv1", "data": "conflict", "version": 2})

    def test_concurrent_write_success_when_version_matches(self) -> None:
        strategy = _make_versioned_strategy()
        strategy.save("vc1", {"id": "vc1", "data": "v0", "version": 0})
        # Update with version=1 — expects DB row with version 0, which is correct
        strategy.save("vc1", {"id": "vc1", "data": "v1", "version": 1})
        result = strategy.find_by_id("vc1")
        assert result is not None
        assert result["data"] == "v1"


# ---------------------------------------------------------------------------
# find_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyFindById:
    def test_returns_none_for_missing(self) -> None:
        strategy = _make_strategy()
        assert strategy.find_by_id("nope") is None

    def test_raises_storage_error_on_failure(self) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_strategy()
        strategy.connection_manager.get_session = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(StorageError):
            strategy.find_by_id("any")


# ---------------------------------------------------------------------------
# find_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyFindAll:
    def test_returns_empty_dict_initially(self) -> None:
        strategy = _make_strategy()
        result = strategy.find_all()
        assert isinstance(result, dict)
        assert len(result) == 0

    def test_returns_all_saved_entities(self) -> None:
        strategy = _make_strategy()
        strategy.save("fa1", {"id": "fa1", "data": "x"})
        strategy.save("fa2", {"id": "fa2", "data": "y"})
        result = strategy.find_all()
        assert "fa1" in result
        assert "fa2" in result

    def test_raises_storage_error_on_failure(self) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_strategy()
        strategy.connection_manager.get_session = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(StorageError):
            strategy.find_all()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyDelete:
    def test_delete_removes_entity(self) -> None:
        strategy = _make_strategy()
        strategy.save("del1", {"id": "del1", "data": "bye"})
        strategy.delete("del1")
        assert strategy.find_by_id("del1") is None

    def test_delete_missing_entity_does_not_raise(self) -> None:
        strategy = _make_strategy()
        strategy.delete("not-there")  # should not raise

    def test_raises_storage_error_on_failure(self) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_strategy()
        strategy.connection_manager.get_session = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(StorageError):
            strategy.delete("any")


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyExists:
    def test_exists_returns_true(self) -> None:
        strategy = _make_strategy()
        strategy.save("ex1", {"id": "ex1"})
        assert strategy.exists("ex1") is True

    def test_exists_returns_false(self) -> None:
        strategy = _make_strategy()
        assert strategy.exists("nope") is False

    def test_exists_returns_false_on_exception(self) -> None:
        strategy = _make_strategy()
        strategy.connection_manager.get_session = MagicMock(side_effect=RuntimeError("boom"))
        result = strategy.exists("any")
        assert result is False


# ---------------------------------------------------------------------------
# find_by_criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyFindByCriteria:
    def test_find_matching_entity(self) -> None:
        strategy = _make_strategy()
        strategy.save("fc1", {"id": "fc1", "data": "alpha"})
        strategy.save("fc2", {"id": "fc2", "data": "beta"})
        result = strategy.find_by_criteria({"data": "alpha"})
        assert len(result) == 1
        assert result[0]["id"] == "fc1"

    def test_no_match_returns_empty(self) -> None:
        strategy = _make_strategy()
        strategy.save("fc3", {"id": "fc3", "data": "gamma"})
        result = strategy.find_by_criteria({"data": "delta"})
        assert result == []

    def test_raises_storage_error_on_failure(self) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_strategy()
        strategy.connection_manager.get_session = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(StorageError):
            strategy.find_by_criteria({"data": "x"})


# ---------------------------------------------------------------------------
# save_batch / delete_batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategySaveBatch:
    def test_save_batch_inserts_multiple(self) -> None:
        strategy = _make_strategy()
        strategy.save_batch(
            {
                "b1": {"id": "b1", "data": "one"},
                "b2": {"id": "b2", "data": "two"},
            }
        )
        assert strategy.exists("b1")
        assert strategy.exists("b2")

    def test_save_batch_raises_storage_error_on_failure(self) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_strategy()
        strategy.connection_manager.get_session = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(StorageError):
            strategy.save_batch({"x": {"id": "x"}})


@pytest.mark.unit
class TestSQLStrategyDeleteBatch:
    def test_delete_batch_removes_entities(self) -> None:
        strategy = _make_strategy()
        strategy.save("db1", {"id": "db1"})
        strategy.save("db2", {"id": "db2"})
        strategy.delete_batch(["db1", "db2"])
        assert not strategy.exists("db1")
        assert not strategy.exists("db2")

    def test_delete_batch_raises_storage_error_on_failure(self) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_strategy()
        strategy.connection_manager.get_session = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(StorageError):
            strategy.delete_batch(["any"])


# ---------------------------------------------------------------------------
# count_by_column
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyCountByColumn:
    def test_count_by_column_unknown_raises_storage_error(self) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_strategy()
        with pytest.raises(StorageError):
            strategy.count_by_column("nonexistent_col")

    def test_count_by_column_returns_counts(self) -> None:
        strategy = SQLStorageStrategy_with_data_col()
        result = strategy.count_by_column("data")
        assert result == {"alpha": 2, "beta": 1}


def SQLStorageStrategy_with_data_col():
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

    strategy = SQLStorageStrategy(
        config={"type": "sqlite", "name": ":memory:"},
        table_name="grp_test",
        columns={"id": "TEXT PRIMARY KEY", "data": "TEXT"},
    )
    strategy.save("g1", {"id": "g1", "data": "alpha"})
    strategy.save("g2", {"id": "g2", "data": "alpha"})
    strategy.save("g3", {"id": "g3", "data": "beta"})
    return strategy


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyCount:
    def test_count_zero_initially(self) -> None:
        strategy = _make_strategy()
        assert strategy.count() == 0

    def test_count_after_inserts(self) -> None:
        strategy = _make_strategy()
        strategy.save("c1", {"id": "c1"})
        strategy.save("c2", {"id": "c2"})
        assert strategy.count() == 2

    def test_count_returns_zero_on_exception(self) -> None:
        strategy = _make_strategy()
        strategy.connection_manager.get_session = MagicMock(side_effect=RuntimeError("boom"))
        assert strategy.count() == 0


# ---------------------------------------------------------------------------
# transaction() context manager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyTransaction:
    def test_transaction_commits_on_success(self) -> None:
        strategy = _make_strategy()
        with strategy.transaction() as session:
            from sqlalchemy import text

            session.execute(
                text("INSERT INTO test_entities (id, data) VALUES (:id, :data)"),
                {"id": "tx1", "data": "val"},
            )
        assert strategy.exists("tx1")

    def test_transaction_rollback_on_exception(self) -> None:
        strategy = _make_strategy()
        with pytest.raises(RuntimeError):
            with strategy.transaction() as session:
                from sqlalchemy import text

                session.execute(
                    text("INSERT INTO test_entities (id, data) VALUES (:id, :data)"),
                    {"id": "txr1", "data": "val"},
                )
                raise RuntimeError("forced rollback")
        assert not strategy.exists("txr1")


# ---------------------------------------------------------------------------
# begin/commit/rollback_transaction (delegated to session)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyTxDelegated:
    def test_begin_transaction_does_not_raise(self) -> None:
        strategy = _make_strategy()
        strategy.begin_transaction()  # no-op

    def test_commit_transaction_does_not_raise(self) -> None:
        strategy = _make_strategy()
        strategy.commit_transaction()  # no-op

    def test_rollback_transaction_does_not_raise(self) -> None:
        strategy = _make_strategy()
        strategy.rollback_transaction()  # no-op


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyCleanup:
    def test_cleanup_closes_connection_manager(self) -> None:
        strategy = _make_strategy()
        strategy.connection_manager.close = MagicMock()
        strategy.cleanup()
        strategy.connection_manager.close.assert_called_once()
